#!/bin/bash
# NeuroVision donanim gecisi: I2S mikrofon (INMP441) + CSI kamera. Hoparlor 3.5 mm jakta kalir.
# Kullanim: sudo bash deploy/hw-setup.sh [kamera]   kamera: ov5647 | imx219 | imx708 | auto (varsayilan)
set -euo pipefail
CFG=/boot/firmware/config.txt
CAM=${1:-auto}
[ "$(id -u)" -eq 0 ] || { echo "sudo ile calistir"; exit 1; }

cp -a "$CFG" "$CFG.before-i2s-$(date +%Y%m%d-%H%M%S)"

# Hoparlor 3.5 mm jakta: analog ses acik kalsin
sed -i "s/^dtparam=audio=off/dtparam=audio=on/" "$CFG"


# Kamera: model belliyse otomatik algilamayi kapatip overlay ile zorla
sed -i "/^dtoverlay=\(ov5647\|imx219\|imx708\|imx477\)/d" "$CFG"
if [ "$CAM" = auto ]; then
    sed -i "s/^camera_auto_detect=0/camera_auto_detect=1/" "$CFG"
else
    sed -i "s/^camera_auto_detect=1/camera_auto_detect=0/" "$CFG"
fi

# [all] bolumune NeuroVision blogu (tekrar calistirilinca yenilenir)
sed -i "/^# >>> neurovision-hw/,/^# <<< neurovision-hw/d" "$CFG"
{
    echo "# >>> neurovision-hw"
    echo "[all]"
    echo "dtoverlay=adau7002-simple,card-name=inmp441"
    [ "$CAM" = auto ] || echo "dtoverlay=$CAM"
    echo "# <<< neurovision-hw"
} >> "$CFG"

echo "---- yeni config.txt (yorumsuz) ----"
grep -vE "^\s*#|^\s*$" "$CFG"
echo "------------------------------------"
echo "Tamam. Simdi: sudo reboot"
