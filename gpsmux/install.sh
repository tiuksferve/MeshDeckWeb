#!/bin/sh
# gpsmux/install.sh — instala (ou remove) a partilha do GPS do uConsole.
#
#   sudo gpsmux/install.sh              instala e liga
#   sudo gpsmux/install.sh --uninstall  remove e repõe o config.yaml
#
# O que faz:
#   1. /etc/systemd/system/meshdeck-gpsmux.service, a correr como o
#      utilizador que chamou o sudo, grupo dialout, antes do meshtasticd;
#   2. drop-in do meshtasticd: arranca depois do gpsmux e reinicia com ele
#      (a porta virtual muda quando o gpsmux reinicia);
#   3. GPS: SerialPath do /etc/meshtasticd/config.yaml → a porta virtual,
#      com cópia do original em config.yaml.before-gpsmux;
#   4. reinicia os dois.
set -eu

UNIT=/etc/systemd/system/meshdeck-gpsmux.service
DROPIN_DIR=/etc/systemd/system/meshtasticd.service.d
DROPIN=$DROPIN_DIR/meshdeck-gpsmux.conf
CONFIG=/etc/meshtasticd/config.yaml
BACKUP=$CONFIG.before-gpsmux
LINK=/run/meshdeck-gps/meshtasticd
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "Corre com sudo: sudo $0 $*" >&2
    exit 1
fi

# SerialPath activo dentro do bloco GPS: (o primeiro não comentado).
current_path() {
    awk '
        /^[^ \t#]/ { in_gps = ($0 ~ /^GPS:[ \t]*$/) }
        in_gps && /^[ \t]+SerialPath:/ { sub(/^[ \t]+SerialPath:[ \t]*/, ""); sub(/[ \t]*#.*/, ""); print; exit }
    ' "${1:-$CONFIG}"
}

set_path() {
    file=${2:-$CONFIG}
    tmp=$(mktemp)
    awk -v new="$1" '
        /^[^ \t#]/ { in_gps = ($0 ~ /^GPS:[ \t]*$/) }
        in_gps && !done && /^[ \t]+SerialPath:/ { match($0, /^[ \t]+/); print substr($0, 1, RLENGTH) "SerialPath: " new; done = 1; next }
        { print }
    ' "$file" > "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now meshdeck-gpsmux.service 2>/dev/null || true
    rm -f "$UNIT" "$DROPIN"
    rmdir "$DROPIN_DIR" 2>/dev/null || true
    if [ -f "$BACKUP" ]; then
        cp -p "$BACKUP" "$CONFIG"
        echo "config.yaml reposto a partir de $BACKUP"
    fi
    systemctl daemon-reload
    systemctl restart meshtasticd.service
    echo "gpsmux removido; o meshtasticd voltou a ler o GPS directamente."
    exit 0
fi

USER_NAME=${SUDO_USER:-}
if [ -z "$USER_NAME" ] || [ "$USER_NAME" = root ]; then
    echo "Corre com sudo a partir do teu utilizador (o serviço corre como ele)." >&2
    exit 1
fi

DEVICE=$(current_path)
if [ "$DEVICE" = "$LINK" ]; then
    # Já instalado: o dispositivo real é o do original.
    DEVICE=$(current_path "$BACKUP")
fi
if [ -z "$DEVICE" ]; then
    echo "O config.yaml não tem GPS: SerialPath activo; nada a partilhar." >&2
    exit 1
fi

cat > "$UNIT" <<EOF
[Unit]
Description=MeshDeck GPS mux: GPS partilhado entre o meshtasticd e outras apps
Before=meshtasticd.service

[Service]
Type=simple
User=$USER_NAME
Group=dialout
RuntimeDirectory=meshdeck-gps
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 $HERE/gpsmux.py --device $DEVICE --link $LINK --tcp 127.0.0.1:10110
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<EOF
# Instalado por gpsmux/install.sh (MeshDeckWeb).
[Unit]
Wants=meshdeck-gpsmux.service
After=meshdeck-gpsmux.service
PartOf=meshdeck-gpsmux.service
EOF

[ -f "$BACKUP" ] || cp -p "$CONFIG" "$BACKUP"
set_path "$LINK"

systemctl daemon-reload
systemctl enable --now meshdeck-gpsmux.service
sleep 1
systemctl restart meshtasticd.service
echo "gpsmux instalado: GPS $DEVICE partilhado."
echo "  meshtasticd → $LINK"
echo "  NMEA ao vivo → tcp://127.0.0.1:10110"
echo "Registo: journalctl -u meshdeck-gpsmux -f"
