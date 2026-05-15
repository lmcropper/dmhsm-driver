#pragma once

#include <Arduino.h>
#include <SPI.h>

class DMHSM {
public:
    DMHSM(int8_t csPin, int8_t rstPin, SPIClass *spi = &SPI);
    void begin();
    void writeRegister(uint8_t reg, uint8_t data);
    void sendFrameBufferDummy();
    void sendFrameBuffer(const uint8_t* buffer);

private:
    int8_t _cs;
    int8_t _rst;
    SPIClass *_spi;

    static const uint8_t REG_WRITE_CMD = 0x78;
    static const uint8_t REG_READ_CMD  = 0x79;
    static const uint8_t DATA_CMD      = 0x02;
};
