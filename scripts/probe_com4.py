"""Quick COM4 SAS link probe."""
import sys, time
import serial
from serial.tools import list_ports

port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
print("Ports:")
for p in list_ports.comports():
    print(f"  {p.device}: {p.description}")
print(f"\n--- probe {port} ---")
for baud in (19200, 921600):
    for rts in (False, True):
        label = f"baud={baud} rts={rts}"
        try:
            ser = serial.Serial(port, baud, bytesize=8, parity=serial.PARITY_SPACE, stopbits=1, timeout=0.2)
            ser.dtr = True
            ser.rts = rts
            time.sleep(0.5)
            ser.reset_input_buffer()
            ser.parity = serial.PARITY_MARK
            ser.write(bytes([0x81]))
            ser.flush(); time.sleep(0.2)
            rx81 = ser.read(256)
            ser.parity = serial.PARITY_NONE
            ser.write(bytes([0x1B, 0x80, 0x81]))
            ser.flush(); time.sleep(0.2)
            rx_mux = ser.read(256)
            ser.parity = serial.PARITY_MARK
            ser.write(bytes([0x80]))
            ser.flush(); time.sleep(0.2)
            rx80 = ser.read(256)
            ser.close()
            print(f"{label}: gp81={rx81.hex() or '-'} mux81={rx_mux.hex() or '-'} gp80={rx80.hex() or '-'}")
        except Exception as exc:
            print(f"{label}: ERROR {exc}")
