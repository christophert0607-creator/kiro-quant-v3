#!/bin/bash
# Kiro Quant SQLite Database Backup Script

DB_FILE="kiro_quant.db"
BACKUP_DIR="backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/kiro_quant_${TIMESTAMP}.db"

mkdir -p "${BACKUP_DIR}"

if [ ! -f "$DB_FILE" ]; then
    echo "Error: Database file $DB_FILE not found."
    exit 1
fi

echo "Starting backup of $DB_FILE..."
cp "$DB_FILE" "$BACKUP_FILE"

if [ -f "$BACKUP_FILE" ]; then
    echo "Compressing backup..."
    gzip "$BACKUP_FILE"
    echo "Backup completed: ${BACKUP_FILE}.gz"
    
    # Keep only last 10 backups
    echo "Cleaning up old backups..."
    ls -t ${BACKUP_DIR}/kiro_quant_*.db.gz | tail -n +11 | xargs -r rm
    echo "Done."
else
    echo "Error: Backup failed."
    exit 1
fi
