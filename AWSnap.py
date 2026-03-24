import boto3
import struct
import os
import sys
import subprocess
import threading
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# --- CONFIGURATION ---
BLOCK_SIZE        = 524288
THREADS           = 50
METADATA_SLURP_MB = 300
SAMPLE_RATE       = 10
LOCAL_MNT         = "./awsnap_mount"

# Thread-local file handles — fixes race condition on concurrent writes
_local = threading.local()

def get_fh(image_name):
    if not hasattr(_local, "fh") or _local.fh.closed:
        _local.fh = open(image_name, "r+b")
    return _local.fh


def print_banner():
    CYAN, MAGENTA, GREEN, GRAY, BOLD, END = (
        "\033[96m", "\033[95m", "\033[92m", "\033[90m", "\033[1m", "\033[0m"
    )
    if os.geteuid() != 0:
        print(f"{BOLD}{MAGENTA}[!] ERROR: AW! snap must be run as root (sudo).{END}")
        sys.exit(1)

    subprocess.run("losetup -D >/dev/null 2>&1", shell=True)

    banner = f"""
{CYAN}{BOLD} █████╗ ██╗    ██╗██╗    ███████╗███╗   ██╗ █████╗ ██████╗ 
██╔══██╗██║    ██║██║    ██╔════╝████╗  ██║██╔══██╗██╔══██╗
███████║██║ █╗ ██║██║    ███████╗██╔██╗ ██║███████║██████╔╝
██╔══██║██║███╗██║╚═╝    ╚════██║██║╚██╗██║██╔══██║██╔═══╝ 
██║  ██║╚███╔███╔╝██╗    ███████║██║ ╚████║██║  ██║██║     
╚═╝  ╚═╝ ╚══╝╚══╝ ╚═╝    ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝     {END}
    {MAGENTA}{BOLD}     >>Snap at Ease:  by @n1chr0x  <<{END}
    {GRAY}--------------------------------------------------{END}
    """
    print(banner)


class AWSnap:
    def __init__(self, ak, sk, rg, snap_id):
        self.snap_id    = snap_id
        self.session    = boto3.Session(
            aws_access_key_id=ak,
            aws_secret_access_key=sk,
            region_name=rg
        )
        self.ebs        = self.session.client("ebs")
        self.ec2        = self.session.client("ec2")
        self.image_name = f"awsnap_{snap_id}.img"

    def get_snap_details(self):
        try:
            res  = self.ec2.describe_snapshots(SnapshotIds=[self.snap_id])
            snap = res["Snapshots"][0]
            print(f"    Size       : {snap['VolumeSize']} GiB")
            print(f"    State      : {snap['State']}")
            print(f"    Description: {snap.get('Description', '(none)')}")
            return snap["VolumeSize"]
        except Exception as e:
            print(f"\033[91m[!] AWS Auth Error: {e}\033[0m")
            sys.exit(1)

    def initialize_sparse(self, size_gb):
        size_bytes = size_gb * 1024 ** 3
        # fallocate is faster; fall back to seek method
        result = subprocess.run(
            ["fallocate", "-l", str(size_bytes), self.image_name],
            capture_output=True
        )
        if result.returncode != 0:
            with open(self.image_name, "wb") as f:
                f.seek(size_bytes - 1)
                f.write(b"\x00")

    def list_blocks(self):
        blocks = []
        resp   = self.ebs.list_snapshot_blocks(SnapshotId=self.snap_id)
        blocks.extend([(b["BlockIndex"], b["BlockToken"]) for b in resp["Blocks"]])
        while "NextToken" in resp:
            resp = self.ebs.list_snapshot_blocks(
                SnapshotId=self.snap_id, NextToken=resp["NextToken"]
            )
            blocks.extend([(b["BlockIndex"], b["BlockToken"]) for b in resp["Blocks"]])
        return blocks

    def write_block(self, block_data):
        """Thread-safe write using thread-local file handles."""
        idx, token = block_data
        try:
            res  = self.ebs.get_snapshot_block(
                SnapshotId=self.snap_id,
                BlockIndex=idx,
                BlockToken=token
            )
            data = res["BlockData"].read()
            fh   = get_fh(self.image_name)
            fh.seek(idx * BLOCK_SIZE)
            fh.write(data)
            fh.flush()
        except Exception as e:
            tqdm.write(f"\033[91m[!] Block {idx} failed: {e}\033[0m")

    def download_batch(self, block_list, label):
        if not block_list:
            return
        with tqdm(total=len(block_list), desc=f"[*] {label}", unit="blk", colour="cyan") as pbar:
            with ThreadPoolExecutor(max_workers=THREADS) as ex:
                futures = [ex.submit(self.write_block, b) for b in block_list]
                for _ in as_completed(futures):
                    pbar.update(1)

    def smart_sample(self, all_blocks, block_start, slurp_count):
        """
        Intelligent sampling — prioritizes contiguous block runs (real file
        data) over uniform sampling which misses critical filesystem structures.

        Strategy:
          1. Metadata region (inode tables, block groups) — downloaded fully
          2. Data region grouped into contiguous runs:
             - Long runs (large files)    — downloaded fully (top 60% by size)
             - Short / scattered runs     — sampled at SAMPLE_RATE
        """
        meta_blocks = all_blocks[block_start: block_start + slurp_count]
        data_blocks = all_blocks[block_start + slurp_count:]

        if not data_blocks:
            return meta_blocks, []

        # Group consecutive block indices into runs
        # Gap > 8 blocks between indices = treat as new run
        runs        = []
        current_run = [data_blocks[0]]
        for block in data_blocks[1:]:
            if block[0] - current_run[-1][0] > 8:
                runs.append(current_run)
                current_run = [block]
            else:
                current_run.append(block)
        if current_run:
            runs.append(current_run)

        # Sort runs by length descending — longer = larger files = higher value
        runs.sort(key=lambda r: len(r), reverse=True)

        # Top 60% of runs by size → download fully
        # Bottom 40% → sample at SAMPLE_RATE
        cutoff          = max(1, int(len(runs) * 0.6))
        priority_blocks = [b for run in runs[:cutoff] for b in run]
        sampled_blocks  = [b for run in runs[cutoff:] for b in run[::SAMPLE_RATE]]

        print(f"    Priority blocks : {len(priority_blocks)} (large contiguous runs — full download)")
        print(f"    Sampled blocks  : {len(sampled_blocks)} (1 in {SAMPLE_RATE} from smaller runs)")

        return meta_blocks, priority_blocks + sampled_blocks


def repair_and_get_offset(img_path):
    print("[*] Repairing corrupted GPT backup headers...")
    subprocess.run(f"sgdisk -e {img_path} >/dev/null 2>&1", shell=True)

    print("[*] Analyzing partition table...")
    try:
        output = subprocess.check_output(
            ["fdisk", "-l", "-u", "sectors", img_path],
            stderr=subprocess.DEVNULL
        ).decode()

        print("\n[*] Partition table (fdisk):")
        for line in output.splitlines():
            print(f"    {line}")

        # Parse logical sector size
        sector_size = 512
        m = re.search(r"Sector size.*?(\d+) bytes", output)
        if m:
            sector_size = int(m.group(1))

        # Find the main data partition — skip small EFI/BIOS partitions
        partitions = []
        for line in output.splitlines():
            m = re.match(r"^\S+\s+(\*)?\s+(\d+)\s+(\d+)\s+(\d+)", line)
            if m:
                bootable     = m.group(1) == "*"
                start_lba    = int(m.group(2))
                sector_count = int(m.group(4))
                if sector_count > 100000:  # skip EFI (~200MB) and BIOS boot (~4MB)
                    partitions.append((bootable, start_lba, sector_count))

        if partitions:
            chosen      = next(
                (p for p in partitions if p[0]),
                max(partitions, key=lambda p: p[2])
            )
            byte_offset = chosen[1] * sector_size
            print(f"\n    Selected LBA start : {chosen[1]}")
            print(f"    Byte offset        : {byte_offset} ({byte_offset // (1024*1024)} MiB)")
            return byte_offset

    except Exception as e:
        print(f"[!] fdisk parsing error: {e}")

    print("[!] Falling back to default offset 116391936 (Sector 227328)")
    return 116391936


def detect_fs_type(dev):
    r = subprocess.run(
        ["blkid", "-o", "value", "-s", "TYPE", dev],
        capture_output=True, text=True
    )
    return r.stdout.strip()


def run_fsck(dev):
    fs_type = detect_fs_type(dev)
    print(f"    Filesystem : {fs_type or 'unknown'}")
    if fs_type in ("ext2", "ext3", "ext4"):
        print(f"    Running    : fsck.ext4 -y -D -f {dev} ...")
        r = subprocess.run(["fsck.ext4", "-y", "-D", "-f", dev], capture_output=True, text=True)
        if r.returncode in (0, 1):
            print(f"\033[92m    fsck OK (rc={r.returncode})\033[0m")
            return True
        print(f"\033[91m    fsck failed (rc={r.returncode})\033[0m")
    elif fs_type == "xfs":
        r = subprocess.run(["xfs_repair", "-L", dev], capture_output=True, text=True)
        if r.returncode == 0:
            print("\033[92m    xfs_repair OK\033[0m")
            return True
    return True


def try_mount_dev(dev, mount_point):
    for opts in ["ro,noload", "ro,norecovery", "ro"]:
        r = subprocess.run(
            ["mount", "-o", opts, dev, mount_point],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print(f"\033[92m[✓] Mounted {dev} at {mount_point} (opts: {opts})\033[0m")
            return True
    return False


def do_mount(image_name, byte_offset, mount_point):
    # Strategy 1: losetup --partscan → kernel finds partitions → fsck → mount
    result = subprocess.run(
        ["losetup", "--find", "--show", "--partscan", image_name],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        loop_dev = result.stdout.strip()
        time.sleep(2)
        for suffix in ["p1", "p2", "p3", "1", "2", ""]:
            dev = f"{loop_dev}{suffix}"
            if not os.path.exists(dev):
                continue
            print(f"\n[*] Trying partition: {dev}")
            run_fsck(dev)
            if try_mount_dev(dev, mount_point):
                return loop_dev
        subprocess.run(["losetup", "-d", loop_dev], capture_output=True)

    # Strategy 2: explicit offset loop + fsck
    print(f"\n[*] Trying offset loop mount (offset={byte_offset}) ...")
    r2 = subprocess.run(
        ["losetup", "--find", "--show", f"--offset={byte_offset}", image_name],
        capture_output=True, text=True
    )
    if r2.returncode == 0:
        loop_dev2 = r2.stdout.strip()
        time.sleep(1)
        run_fsck(loop_dev2)
        if try_mount_dev(loop_dev2, mount_point):
            return loop_dev2
        subprocess.run(["losetup", "-d", loop_dev2], capture_output=True)

    # Strategy 3: direct offset mount
    print("\n[*] Trying direct offset mount ...")
    for opts in [f"ro,noload,loop,offset={byte_offset}",
                 f"ro,loop,offset={byte_offset}"]:
        r = subprocess.run(
            ["mount", "-o", opts, image_name, mount_point],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print(f"\033[92m[✓] Mounted with offset={byte_offset}\033[0m")
            return None

    print(f"\033[91m[!] Mount failed. Kernel error: {r.stderr.strip()}\033[0m")
    subprocess.run("losetup -D", shell=True)
    return None


if __name__ == "__main__":
    print_banner()

    print(f"\033[94m[#] AWS CONFIGURATION\033[0m")
    ak  = input("    Access Key ID    : ").strip()
    sk  = input("    Secret Access Key: ").strip()
    rg  = input("    Default Region   : ").strip() or "us-east-1"
    sid = input("\n\033[95m[?] Snapshot ID      : \033[0m").strip()

    if not sid.startswith("snap-"):
        print("[!] Invalid snapshot ID.")
        sys.exit(1)

    worker = AWSnap(ak, sk, rg, sid)

    print("\n[*] Fetching snapshot details ...")
    size = worker.get_snap_details()

    print(f"[*] Volume Size: {size}GB. Initializing sparse file...")
    worker.initialize_sparse(size)

    print("\n[*] Listing all blocks ...")
    all_blocks = worker.list_blocks()
    print(f"    Total blocks: {len(all_blocks)}")

    # Phase 1: probe 34 blocks — covers full GPT partition table
    # (old code used slurp_count here which skipped proper partition detection)
    probe_count = min(34, len(all_blocks))
    worker.download_batch(all_blocks[:probe_count], "Probing Partition Table")

    # Get accurate offset via fdisk + sgdisk repair
    exact_offset = repair_and_get_offset(worker.image_name)
    block_start  = exact_offset // BLOCK_SIZE
    slurp_count  = (METADATA_SLURP_MB * 1024 * 1024) // BLOCK_SIZE

    print(f"\n    Byte offset : {exact_offset} ({exact_offset // (1024*1024)} MiB)")
    print(f"    Block start : {block_start}")

    # Phase 2 + 3: smart sampling replaces uniform sampling
    print("\n[*] Computing smart sample ...")
    meta_blocks, data_blocks = worker.smart_sample(all_blocks, block_start, slurp_count)

    worker.download_batch(meta_blocks, "Downloading Headers")
    worker.download_batch(data_blocks, "Smart Sampling Content")

    # Mount
    full_path = os.path.abspath(LOCAL_MNT)
    os.makedirs(full_path, exist_ok=True)

    print(f"\n[*] Attempting mount with offset {exact_offset}...")
    loop_dev = do_mount(worker.image_name, exact_offset, full_path)

    if os.path.ismount(full_path):
        print(f"\033[92m[✓] MOUNTED SUCCESSFULLY at {full_path}\033[0m")
        input(f"\n\033[93m[!] Press Enter to UNMOUNT and CLEANUP session...\033[0m")
        subprocess.run(f"umount -l {full_path}", shell=True)
        subprocess.run("losetup -D", shell=True)
        print("\033[92m[+] Cleanup complete.\033[0m")
    else:
        print(f"\033[91m[!] Mount failed. Try manually:\033[0m")
        print(f"    sudo fdisk -l {worker.image_name}")
        print(f"    sudo fsck.ext4 -y -D /dev/loopXp1")
        print(f"    sudo mount -o ro,noload /dev/loopXp1 {full_path}")
        subprocess.run("losetup -D", shell=True)
