CC ?= gcc
CFLAGS ?= -O2 -Wall -fPIC
SRC = encoder.c

UNAME_S := $(shell uname -s 2>/dev/null || echo Windows)

ifeq ($(OS),Windows_NT)
    TARGET = aetherix_native.dll
    LDFLAGS = -shared
else ifeq ($(UNAME_S),Darwin)
    TARGET = libaetherix_native.dylib
    LDFLAGS = -shared
else
    TARGET = libaetherix_native.so
    LDFLAGS = -shared
endif

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(LDFLAGS) $(CFLAGS) -o $(TARGET) $(SRC)

clean:
	rm -f *.so *.dylib *.dll
