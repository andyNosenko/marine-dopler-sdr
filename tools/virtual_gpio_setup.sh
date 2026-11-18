#!/bin/bash
# Создаёт виртуальный gpiochip с 3 линиями: A, B, GATE
set -e

CHIP_NAME="${1:-rdf_sim}"
NUM_LINES=3

sudo modprobe gpio-sim 2>/dev/null || true
sudo modprobe configfs 2>/dev/null || true
sudo mountpoint -q /sys/kernel/config || sudo mount -t configfs configfs /sys/kernel/config

GPIO_SIM=/sys/kernel/config/gpio-sim
if [ ! -d "$GPIO_SIM" ]; then
  echo "ERROR: gpio-sim configfs не найден. Загружен ли модуль?" >&2
  exit 1
fi

# Удалить предыдущий, если был
if [ -d "$GPIO_SIM/$CHIP_NAME" ]; then
  echo 0 | sudo tee "$GPIO_SIM/$CHIP_NAME/live" >/dev/null 2>&1 || true
  sudo rmdir "$GPIO_SIM/$CHIP_NAME" 2>/dev/null || true
fi

sudo mkdir -p "$GPIO_SIM/$CHIP_NAME"
sudo mkdir -p "$GPIO_SIM/$CHIP_NAME/gpio-bank0"
echo $NUM_LINES | sudo tee "$GPIO_SIM/$CHIP_NAME/gpio-bank0/num_lines" >/dev/null

# Имена линий (для отладки)
for i in $(seq 0 $((NUM_LINES-1))); do
  sudo mkdir -p "$GPIO_SIM/$CHIP_NAME/gpio-bank0/line$i"
done
echo "LINE_A"   | sudo tee "$GPIO_SIM/$CHIP_NAME/gpio-bank0/line0/name" >/dev/null
echo "LINE_B"   | sudo tee "$GPIO_SIM/$CHIP_NAME/gpio-bank0/line1/name" >/dev/null
echo "GATE_REF" | sudo tee "$GPIO_SIM/$CHIP_NAME/gpio-bank0/line2/name" >/dev/null

# Активировать чип
echo 1 | sudo tee "$GPIO_SIM/$CHIP_NAME/live" >/dev/null

echo "OK: создан виртуальный GPIO-чип '$CHIP_NAME' с $NUM_LINES линиями"
echo "Проверьте: gpiodetect"
