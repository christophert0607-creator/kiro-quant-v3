import os
import shutil
import glob

base_dir = "/home/tsukii0607/.openclaw/workspace/skills/kiro-quant"
archive_dir = os.path.join(base_dir, "legacy_archive")
docs_dir = os.path.join(archive_dir, "dev_logs")

os.makedirs(docs_dir, exist_ok=True)

# List of specific files to move
doc_files = [
    "PROGRESS.md",
    "progress_hourly_review.md",
    "STAGE_COMPLETION.md",
    "ROADMAP.md",
    "issues.md",
    "PUBLISH_README.md",
]

for doc in doc_files:
    src = os.path.join(base_dir, doc)
    if os.path.exists(src):
        try:
            shutil.move(src, os.path.join(docs_dir, doc))
            print(f"Moved {doc} to {docs_dir}")
        except Exception as e:
            print(f"Error moving {doc}: {e}")

# Move all HOURLY_REVIEW files
for review_file in glob.glob(os.path.join(base_dir, "HOURLY_REVIEW_*.md")):
    filename = os.path.basename(review_file)
    try:
        shutil.move(review_file, os.path.join(docs_dir, filename))
        print(f"Archived log {filename}")
    except Exception as e:
        print(f"Error moving {filename}: {e}")

print("Doc cleanup complete!")
