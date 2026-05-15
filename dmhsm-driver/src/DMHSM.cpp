#include "DMHSM.h"

DMHSM::DMHSM(int8_t csPin, int8_t rstPin, SPIClass *spi) 
    : _cs(csPin), _rst(rstPin), _spi(spi) {}

void DMHSM::begin() {
    pinMode(_cs, OUTPUT);
    pinMode(_rst, OUTPUT);
    digitalWrite(_cs, HIGH);

    // Hardware reset
    digitalWrite(_rst, LOW);
    delay(10);
    digitalWrite(_rst, HIGH);
    delay(50);

    // Initial config from datasheet (e.g., set pattern for self test)
    writeRegister(0x00, 0x02); // 10: GRAY256 data format
    writeRegister(0x1B, 0x00); // 0: Disable internal test pattern to show custom data
    writeRegister(0x1C, 0x07); // Enable PWM dimming max level
}

void DMHSM::writeRegister(uint8_t reg, uint8_t data) {
    _spi->beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));
    digitalWrite(_cs, LOW);
    _spi->transfer(REG_WRITE_CMD);
    _spi->transfer(reg);
    _spi->transfer(data);
    digitalWrite(_cs, HIGH);
    _spi->endTransaction();
}

void DMHSM::sendFrameBufferDummy() {
    _spi->beginTransaction(SPISettings(20000000, MSBFIRST, SPI_MODE0));
    digitalWrite(_cs, LOW);
    _spi->transfer(DATA_CMD);
    _spi->transfer(0x00);
    _spi->transfer(0x2C);
    _spi->transfer(0x00);

    // Sending dummy lines to emulate frame
    uint8_t dummyLine[640];
    memset(dummyLine, 0xAA, 640);
    for (int i = 0; i < 480; i++) {
        _spi->writeBytes(dummyLine, 640);
    }

    digitalWrite(_cs, HIGH);
    _spi->endTransaction();
}

void DMHSM::sendFrameBuffer(const uint8_t* buffer) {
    if (!buffer) return;

    _spi->beginTransaction(SPISettings(20000000, MSBFIRST, SPI_MODE0));
    digitalWrite(_cs, LOW);
    _spi->transfer(DATA_CMD);
    _spi->transfer(0x00);
    _spi->transfer(0x2C);
    _spi->transfer(0x00);

    // Write out the entire buffer line by line
    for (int i = 0; i < 480; i++) {
        _spi->writeBytes((uint8_t*)&buffer[i * 640], 640);
    }

    digitalWrite(_cs, HIGH);
    _spi->endTransaction();
}
