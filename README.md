# AWSnap ⚡
### **High-Speed EBS Snapshot Triage & Smart Exfiltration**
**Author:** @n1chr0x  
**License:** MIT

**AWSnap** is an offensive security tool built for the "Smash & Grab" phase of a cloud engagement. It allows you to mount and browse AWS EBS Snapshots locally in seconds by downloading only the critical bits of data (Metadata & Inodes) rather than the whole disk.

---

## 🚀 Why is AWSnap Special?

Most tools (like Pacu) are like moving an entire house just to find a spare key. **AWSnap** is like a specialized locksmith—it only grabs what matters.

* **Smart Sampling:** It "slurps" the filesystem map (Inode tables) first, so you can `ls` the drive immediately.
* **Sparse File Magic:** It creates a virtual 100GB disk that takes up almost zero space on your computer until you start reading files.
* **Auto-Repair:** It automatically fixes "Corrupt GPT" headers and aligns partitions so you don't have to manually calculate offsets.
* **Stealthy:** Operates purely via EBS Direct APIs. No EC2 instances are launched, keeping your footprint tiny.



---

## 📊 AWSnap vs. Pacu (dsnap)

| Feature | Pacu (`ebs__download`) | **AWSnap** |
| :--- | :--- | :--- |
| **Speed** | 🐢 Slow (Downloads 100%) | ⚡ **Fast (Downloads ~5%)** |
| **Storage** | Needs full disk space | **Uses almost zero space** |
| **Method** | Full Forensic Image | **Smart Triage / Sampling** |
| **Best For** | Deep Law Enforcement Work | **Red Teaming & Secret Hunting** |

---

## 🛠️ Easy Installation

We’ve made it simple for you. Just run the setup script to grab all the "hardware" and "software" tools you need.

```bash
# 1. Clone the repo
git clone [https://github.com/n1chr0x/AWSnap.git](https://github.com/n1chr0x/AWSnap.git)
cd AWSnap

# 2. Run the auto-setup (as root)
sudo chmod +x setup.sh
sudo ./setup.sh
