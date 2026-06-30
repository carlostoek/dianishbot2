#!/bin/bash
#
# Backup automático diario de diana_system_prompt.md (no versionado en git)
# Usa rclone (ya configurado como "aws:")
#
# Uso:
#   ./scripts/backup-system-prompt.sh
#   (se puede ejecutar manualmente para probar)
#
set -euo pipefail

# === CONFIGURACIÓN ===
PROJECT_DIR="/home/ubuntu/repos/diana"
PROMPT_FILE="${PROJECT_DIR}/diana_system_prompt.md"

# Remote de rclone (ya autenticado)
REMOTE="aws"

# Carpeta de destino en el remoto.
# Cambiá esto si querés otro lugar (ej: "Backups/diana/prompt" o "mi-bucket/diana")
BACKUP_BASE="diana-backups/system-prompt"

# Log
LOG_FILE="${HOME}/diana-prompt-backup.log"

# === LÓGICA ===
DATE=$(date +%F)

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "[$(date '+%F %T')] ERROR: No se encontró $PROMPT_FILE" | tee -a "$LOG_FILE"
  exit 1
fi

echo "[$(date '+%F %T')] Iniciando backup del system prompt..." | tee -a "$LOG_FILE"

# 1. Copia a carpeta fechada (mantiene histórico diario)
rclone copyto \
  "$PROMPT_FILE" \
  "${REMOTE}:${BACKUP_BASE}/${DATE}/diana_system_prompt.md" \
  --log-file "$LOG_FILE" \
  --log-level INFO

# 2. Copia a "latest" (fácil acceso al último)
rclone copyto \
  "$PROMPT_FILE" \
  "${REMOTE}:${BACKUP_BASE}/latest/diana_system_prompt.md" \
  --log-file "$LOG_FILE" \
  --log-level INFO

echo "[$(date '+%F %T')] Backup completado → ${BACKUP_BASE}/${DATE}/ y latest/" | tee -a "$LOG_FILE"
