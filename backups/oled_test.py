import time
import board
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306


def main():
    # I2C on Raspberry Pi: SCL=GPIO3, SDA=GPIO2
    i2c = busio.I2C(board.SCL, board.SDA)

    # 0x3C is the common SSD1306 address and matches your i2cdetect result
    oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c, addr=0x3C)

    # Clear display
    oled.fill(0)
    oled.show()

    # Draw buffer
    image = Image.new("1", (oled.width, oled.height))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    draw.text((0, 0), "airBuddy v1", font=font, fill=255)
    draw.text((0, 18), "OLED detected", font=font, fill=255)
    draw.text((0, 36), "addr: 0x3C", font=font, fill=255)

    oled.image(image)
    oled.show()

    time.sleep(10)

    # Clear after 10 seconds
    oled.fill(0)
    oled.show()


if __name__ == "__main__":
    main()
