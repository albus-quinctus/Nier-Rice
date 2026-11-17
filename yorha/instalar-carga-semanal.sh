#!/bin/sh
# instalar-carga-semanal.sh — una vez por semana, carga las baterías hasta el 100 % y después
# vuelve al límite normal (75-80 %). Ayuda a que el indicador de carga no se descalibre.
# Uso, una vez y como root: sh /srv/nier/yorha/instalar-carga-semanal.sh
# Para quitarlo: systemctl disable --now carga-completa.timer
set -eu
[ "$(id -u)" = 0 ] || { echo "Ejecútalo como root (su -)." >&2; exit 1; }

cat > /usr/local/sbin/carga-completa <<'EOF'
#!/bin/sh
# carga-completa — sube el límite al 100 %, espera a que todas las baterías estén llenas
# (o a que pasen 24 h) y deja otra vez 75-80 %. Lo lanza carga-completa.timer.
# Si se reinicia a medias, la regla udev ya devuelve el límite a 75-80 % al arrancar.
umbral() {
  for b in /sys/class/power_supply/BAT*; do
    [ -w "$b/charge_control_end_threshold" ] || continue
    # El orden importa: el inicio nunca puede quedar por encima del final
    if [ "$2" -gt "$(cat "$b/charge_control_end_threshold")" ]; then
      echo "$2" > "$b/charge_control_end_threshold"; echo "$1" > "$b/charge_control_start_threshold"
    else
      echo "$1" > "$b/charge_control_start_threshold"; echo "$2" > "$b/charge_control_end_threshold"
    fi
  done
}
llenas() {
  for b in /sys/class/power_supply/BAT*; do
    [ -r "$b/capacity" ] || continue
    [ "$(cat "$b/status")" = Full ] || [ "$(cat "$b/capacity")" -ge 99 ] || return 1
  done
}
trap 'umbral 75 80' EXIT
trap 'exit 1' INT TERM
umbral 95 100
echo "Carga completa: límite al 100 %"
i=0
while [ "$i" -lt 288 ] && ! llenas; do sleep 300; i=$((i + 1)); done
echo "Carga completa terminada; límite otra vez en 75-80 %"
EOF
chmod 755 /usr/local/sbin/carga-completa

cat > /etc/systemd/system/carga-completa.service <<'EOF'
[Unit]
Description=Carga semanal de las baterías al 100 %

[Service]
Type=simple
ExecStart=/usr/local/sbin/carga-completa
EOF

cat > /etc/systemd/system/carga-completa.timer <<'EOF'
[Unit]
Description=Carga semanal de las baterías al 100 % (domingos)

[Timer]
OnCalendar=Sun 10:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now carga-completa.timer
echo "Hecho. Próxima carga completa:"
systemctl list-timers carga-completa.timer --no-pager | sed -n 2p
