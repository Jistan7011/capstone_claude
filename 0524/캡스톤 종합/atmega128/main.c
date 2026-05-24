/*
 * ATmega128 Line Tracer + PN532 RFID + Bluetooth + Jetson Telemetry
 *
 * Jetson Nano <-> USART0 <-> ATmega128
 *   Jetson TX -> ATmega RX0 : F/L/R/S (AUTO mode only)
 *   ATmega TX0 -> Jetson RX : telemetry line
 *     STAT,mode=AUTO,direction=LEFT,rpm_l=12.3,rpm_r=15.1,zone=A,age_ms=40,speed=120
 *
 * Bluetooth (USART1)
 *   A/M : AUTO/MANUAL
 *   w/s/l/r/x : manual control
 *   0~9, +, - : speed
 *   space : emergency stop
 *   Current direction/RPM/zone also printed periodically.
 */

#define F_CPU 16000000UL

#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include "pn532.h"

#define DIR1 PB5
#define DIR3 PA3

#define LEFT_ENCODER_PULSE PE7
#define LEFT_ENCODER_DIR   PE5
#define RIGHT_ENCODER_PULSE PD4
#define RIGHT_ENCODER_DIR   PD5
#define PPR 95
#define COMMAND_TIMEOUT_MS 300

volatile char rpi_command = 'S';
volatile uint8_t is_auto_mode = 0;
volatile char bt_command = 0;
volatile int manual_speed = 150;
volatile uint16_t jetson_cmd_age_ms = 1000;

volatile long left_pulse_count = 0;
volatile int left_direction = 1;
volatile uint32_t left_ovf = 0;
volatile uint32_t left_period_ticks = 0;
volatile uint8_t left_new_pulse = 0;
volatile uint16_t left_no_pulse_timer = 0;
double left_rpm = 0;

volatile long right_pulse_count = 0;
volatile int right_direction = 1;
volatile uint32_t right_ovf = 0;
volatile uint32_t right_period_ticks = 0;
volatile uint8_t right_new_pulse = 0;
volatile uint16_t right_no_pulse_timer = 0;
double right_rpm = 0;

static char current_direction[12] = "STOP";
static char current_zone[24] = "-";
static uint8_t telemetry_dirty = 1;

static inline void uart0_tx(char data) {
    while (!(UCSR0A & (1 << UDRE0)));
    UDR0 = data;
}
static void uart0_print(const char *str) {
    while (*str) uart0_tx(*str++);
}
static inline void uart1_tx(char data) {
    while (!(UCSR1A & (1 << UDRE1)));
    UDR1 = data;
}
static void uart1_print(const char *str) {
    while (*str) uart1_tx(*str++);
}

static void uart0_init_rpi(void) {
    UCSR0A |= (1 << U2X0);
    UBRR0H = 0;
    UBRR0L = 16;   // 115200 @ 16MHz double speed
    UCSR0B = (1 << RXEN0) | (1 << TXEN0) | (1 << RXCIE0);
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
}
static void uart1_init_bt(void) {
    UCSR1A |= (1 << U2X1);
    UBRR1H = 0;
    UBRR1L = 16;   // 115200 @ 16MHz double speed
    UCSR1B = (1 << RXEN1) | (1 << TXEN1) | (1 << RXCIE1);
    UCSR1C = (1 << UCSZ11) | (1 << UCSZ10);
}

static const char* cmd_to_direction(char cmd) {
    switch (cmd) {
        case 'F': return "FORWARD";
        case 'L': return "LEFT";
        case 'R': return "RIGHT";
        case 'S': return "STOP";
        default:  return "STOP";
    }
}

static void set_current_direction(const char* dir) {
    if (strcmp(current_direction, dir) != 0) {
        strncpy(current_direction, dir, sizeof(current_direction) - 1);
        current_direction[sizeof(current_direction) - 1] = '\0';
        telemetry_dirty = 1;
    }
}

ISR(USART0_RX_vect) {
    char received_char = UDR0;

    if (received_char == 'A') {
        is_auto_mode = 1;
        bt_command = 0;
        rpi_command = 'S';
        jetson_cmd_age_ms = 1000;
        set_current_direction("STOP");
        telemetry_dirty = 1;
        return;
    }
    if (received_char == 'M') {
        is_auto_mode = 0;
        rpi_command = 'S';
        bt_command = 0;
        jetson_cmd_age_ms = 1000;
        set_current_direction("STOP");
        telemetry_dirty = 1;
        return;
    }

    if (received_char == 'F' || received_char == 'L' || received_char == 'R' || received_char == 'S') {
        if (is_auto_mode) {
            rpi_command = received_char;
            jetson_cmd_age_ms = 0;
            set_current_direction(cmd_to_direction(received_char));
        } else {
            if (received_char == 'F') {
                bt_command = 'w';
                set_current_direction("FORWARD");
            } else if (received_char == 'L') {
                bt_command = 'l';
                set_current_direction("LEFT");
            } else if (received_char == 'R') {
                bt_command = 'r';
                set_current_direction("RIGHT");
            } else {
                bt_command = 0;
                manual_speed = 0;
                set_current_direction("STOP");
            }
            telemetry_dirty = 1;
        }
    }

    if (!is_auto_mode) {
        if (received_char >= '0' && received_char <= '9') {
            if (received_char == '0') manual_speed = 255;
            else manual_speed = (received_char - '0') * 25;
            telemetry_dirty = 1;
        } else if (received_char == '+') {
            manual_speed += 10;
            if (manual_speed > 255) manual_speed = 255;
            telemetry_dirty = 1;
        } else if (received_char == '-') {
            manual_speed -= 10;
            if (manual_speed < 0) manual_speed = 0;
            telemetry_dirty = 1;
        }
    }
}

ISR(USART1_RX_vect) {
    char rx_char = UDR1;
    char msg_buf[64];

    if (rx_char == ' ') {
        is_auto_mode = 0;
        bt_command = 0;
        manual_speed = 0;
        rpi_command = 'S';
        set_current_direction("STOP");
        telemetry_dirty = 1;
        uart1_print("!! EMERGENCY STOP !!\r\n");
        return;
    }

    if (rx_char == 'A' || rx_char == 'a') {
        is_auto_mode = 1;
        bt_command = 0;
        telemetry_dirty = 1;
        uart1_print("Mode: AUTO\r\n");
        return;
    }
    if (rx_char == 'M' || rx_char == 'm') {
        is_auto_mode = 0;
        rpi_command = 'S';
        bt_command = 0;
        telemetry_dirty = 1;
        set_current_direction("STOP");
        uart1_print("Mode: MANUAL\r\n");
        return;
    }

    if (!is_auto_mode) {
        if (rx_char == 'x' || rx_char == 'X') {
            bt_command = 0;
            manual_speed = 0;
            set_current_direction("STOP");
            telemetry_dirty = 1;
            uart1_print("CMD: x (Manual Stop)\r\n");
        }
        else if (rx_char >= '0' && rx_char <= '9') {
            if (rx_char == '0') manual_speed = 255;
            else manual_speed = (rx_char - '0') * 25;
            telemetry_dirty = 1;
            sprintf(msg_buf, "Speed Set: %d\r\n", manual_speed);
            uart1_print(msg_buf);
        }
        else if (rx_char == '+') {
            manual_speed += 10;
            if (manual_speed > 255) manual_speed = 255;
            telemetry_dirty = 1;
            sprintf(msg_buf, "Speed UP: %d\r\n", manual_speed);
            uart1_print(msg_buf);
        }
        else if (rx_char == '-') {
            manual_speed -= 10;
            if (manual_speed < 0) manual_speed = 0;
            telemetry_dirty = 1;
            sprintf(msg_buf, "Speed DOWN: %d\r\n", manual_speed);
            uart1_print(msg_buf);
        }
        else if (rx_char == 'w' || rx_char == 'W' ||
                 rx_char == 's' || rx_char == 'S' ||
                 rx_char == 'l' || rx_char == 'L' ||
                 rx_char == 'r' || rx_char == 'R') {
            bt_command = rx_char;
            if (rx_char == 'w' || rx_char == 'W') set_current_direction("FORWARD");
            else if (rx_char == 's' || rx_char == 'S') set_current_direction("REVERSE");
            else if (rx_char == 'l' || rx_char == 'L') set_current_direction("LEFT");
            else if (rx_char == 'r' || rx_char == 'R') set_current_direction("RIGHT");
            telemetry_dirty = 1;
            sprintf(msg_buf, "CMD: %c (Spd:%d)\r\n", rx_char, manual_speed);
            uart1_print(msg_buf);
        }
    }
}

static void motor_init(void) {
    DDRB |= (1<<PB4) | (1<<DIR1);
    DDRB |= (1<<PB7);
    DDRA |= (1<<DIR3);
    TCCR0 = (1<<WGM00) | (1<<WGM01) | (1<<COM01) | (1<<CS01);
    TCCR2 = (1<<WGM20) | (1<<WGM21) | (1<<COM21) | (1<<CS21);
    OCR0 = 0;
    OCR2 = 0;
}
static void encoder_init(void) {
    DDRE &= ~((1<<LEFT_ENCODER_PULSE) | (1<<LEFT_ENCODER_DIR));
    PORTE |= (1<<LEFT_ENCODER_PULSE) | (1<<LEFT_ENCODER_DIR);
    DDRD &= ~((1<<RIGHT_ENCODER_PULSE) | (1<<RIGHT_ENCODER_DIR));
    PORTD |= (1<<RIGHT_ENCODER_PULSE) | (1<<RIGHT_ENCODER_DIR);

    TCCR3A = 0;
    TCCR3B = (1 << ICES3) | (1 << CS30);
    ETIMSK |= (1 << TICIE3) | (1 << TOIE3);
    TCNT3 = 0;

    TCCR1A = 0;
    TCCR1B = (1 << ICES1) | (1 << CS10);
    TIMSK |= (1 << TICIE1) | (1 << TOIE1);
    TCNT1 = 0;

    sei();
}
ISR(TIMER3_OVF_vect) { left_ovf++; }
ISR(TIMER3_CAPT_vect) {
    uint16_t cap = ICR3;
    uint32_t now = ((uint32_t)left_ovf << 16) | cap;
    static uint32_t prev_l = 0;
    uint32_t diff = now - prev_l;
    if (diff < 1000000UL) left_period_ticks = diff;
    prev_l = now;
    if (PINE & (1<<LEFT_ENCODER_DIR)) left_direction = 1; else left_direction = -1;
    left_pulse_count += left_direction;
    left_new_pulse = 1;
    left_no_pulse_timer = 0;
}
ISR(TIMER1_OVF_vect) { right_ovf++; }
ISR(TIMER1_CAPT_vect) {
    uint16_t cap = ICR1;
    uint32_t now = ((uint32_t)right_ovf << 16) | cap;
    static uint32_t prev_r = 0;
    uint32_t diff = now - prev_r;
    if (diff < 1000000UL) right_period_ticks = diff;
    prev_r = now;
    if (PIND & (1<<RIGHT_ENCODER_DIR)) right_direction = 1; else right_direction = -1;
    right_pulse_count += right_direction;
    right_new_pulse = 1;
    right_no_pulse_timer = 0;
}

static void set_motor_speed(int left_pwm, int right_pwm, uint8_t left_dir, uint8_t right_dir) {
    if (left_pwm < 0) left_pwm = -left_pwm;
    if (right_pwm < 0) right_pwm = -right_pwm;
    if (left_pwm > 255) left_pwm = 255;
    if (right_pwm > 255) right_pwm = 255;
    OCR0 = (uint8_t)left_pwm;
    OCR2 = (uint8_t)right_pwm;
    if (left_dir == 1) PORTB |= (1<<DIR1); else PORTB &= ~(1<<DIR1);
    if (right_dir == 1) PORTA |= (1<<DIR3); else PORTA &= ~(1<<DIR3);
}

static void line_follow_logic(void) {
    const int BASE_SPEED = 120;
    const int TURN_SPEED = 60;
    if (jetson_cmd_age_ms > COMMAND_TIMEOUT_MS) {
        set_motor_speed(0, 0, 1, 1);
        set_current_direction("STOP");
        return;
    }
    switch (rpi_command) {
        case 'F':
            set_motor_speed(BASE_SPEED, BASE_SPEED, 1, 1);
            set_current_direction("FORWARD");
            break;
        case 'L':
            set_motor_speed(TURN_SPEED, BASE_SPEED, 1, 1);
            set_current_direction("LEFT");
            break;
        case 'R':
            set_motor_speed(BASE_SPEED, TURN_SPEED, 1, 1);
            set_current_direction("RIGHT");
            break;
        case 'S':
        default:
            set_motor_speed(0, 0, 1, 1);
            set_current_direction("STOP");
            break;
    }
}

static void manual_control_logic(void) {
    switch (bt_command) {
        case 'w': case 'W':
            set_motor_speed(manual_speed, manual_speed, 1, 1);
            set_current_direction("FORWARD");
            break;
        case 's': case 'S':
            set_motor_speed(manual_speed, manual_speed, 0, 0);
            set_current_direction("REVERSE");
            break;
        case 'l': case 'L':
            set_motor_speed(manual_speed/2, manual_speed, 1, 1);
            set_current_direction("LEFT");
            break;
        case 'r': case 'R':
            set_motor_speed(manual_speed, manual_speed/2, 1, 1);
            set_current_direction("RIGHT");
            break;
        default:
            set_motor_speed(0, 0, 1, 1);
            set_current_direction("STOP");
            break;
    }
}

static PN532 nfc;
static uint8_t pn532_ok = 0;
static uint8_t last_uid[10];
static uint8_t last_uid_len = 0;
static uint16_t uid_hold_ms = 0;

static inline uint8_t twi_status(void) { return (uint8_t)(TWSR & 0xF8); }
static uint8_t TWI_start(void) {
    TWCR = (1<<TWINT) | (1<<TWSTA) | (1<<TWEN);
    while (!(TWCR & (1<<TWINT)));
    uint8_t st = twi_status();
    return (st == 0x08 || st == 0x10);
}
static void TWI_stop(void) {
    TWCR = (1<<TWINT) | (1<<TWSTO) | (1<<TWEN);
    while (TWCR & (1<<TWSTO));
}
static uint8_t TWI_write(uint8_t data) {
    TWDR = data;
    TWCR = (1<<TWINT) | (1<<TWEN);
    while (!(TWCR & (1<<TWINT)));
    return twi_status();
}
static uint8_t TWI_read_ack(void) {
    TWCR = (1<<TWINT) | (1<<TWEN) | (1<<TWEA);
    while (!(TWCR & (1<<TWINT)));
    return TWDR;
}
static uint8_t TWI_read_nack(void) {
    TWCR = (1<<TWINT) | (1<<TWEN);
    while (!(TWCR & (1<<TWINT)));
    return TWDR;
}
static void TWI_init(void) {
    PORTD |= (1<<PD0) | (1<<PD1);
    TWSR = 0x00;
    TWBR = 72;
    TWCR = (1<<TWEN);
}
#define PN532_ADDR7  0x24
#define SLA_W        ((PN532_ADDR7<<1) | 0)
#define SLA_R        ((PN532_ADDR7<<1) | 1)
int AVR_PN532_I2C_WriteData(uint8_t *data, uint16_t count) {
    if (!TWI_start()) return PN532_STATUS_ERROR;
    if (TWI_write(SLA_W) != 0x18) { TWI_stop(); return PN532_STATUS_ERROR; }
    if (TWI_write(0x00) != 0x28) { TWI_stop(); return PN532_STATUS_ERROR; }
    for (uint16_t i = 0; i < count; i++) {
        if (TWI_write(data[i]) != 0x28) { TWI_stop(); return PN532_STATUS_ERROR; }
    }
    TWI_stop();
    return PN532_STATUS_OK;
}
int AVR_PN532_I2C_ReadData(uint8_t *data, uint16_t count) {
    if (!TWI_start()) return PN532_STATUS_ERROR;
    if (TWI_write(SLA_R) != 0x40) { TWI_stop(); return PN532_STATUS_ERROR; }
    uint8_t status = TWI_read_nack();
    TWI_stop();
    if (status != 0x01) return PN532_STATUS_ERROR;
    if (!TWI_start()) return PN532_STATUS_ERROR;
    if (TWI_write(SLA_R) != 0x40) { TWI_stop(); return PN532_STATUS_ERROR; }
    (void)TWI_read_ack();
    for (uint16_t i = 0; i < count; i++) {
        data[i] = (i == count - 1) ? TWI_read_nack() : TWI_read_ack();
    }
    TWI_stop();
    return PN532_STATUS_OK;
}
bool AVR_PN532_WaitReady(uint32_t timeout_ms) {
    while (timeout_ms--) {
        if (!TWI_start()) { TWI_stop(); _delay_ms(1); continue; }
        if (TWI_write(SLA_R) != 0x40) { TWI_stop(); _delay_ms(1); continue; }
        uint8_t status = TWI_read_nack();
        TWI_stop();
        if (status == 0x01) return true;
        _delay_ms(1);
    }
    return false;
}
int AVR_PN532_Wakeup(void) { _delay_ms(10); return PN532_STATUS_OK; }
static void PN532_init(void) {
    uint8_t ver[8];
    nfc.write_data = AVR_PN532_I2C_WriteData;
    nfc.read_data  = AVR_PN532_I2C_ReadData;
    nfc.wait_ready = AVR_PN532_WaitReady;
    nfc.wakeup     = AVR_PN532_Wakeup;
    nfc.log        = uart1_print;
    _delay_ms(200);
    if (PN532_GetFirmwareVersion(&nfc, ver) == PN532_STATUS_OK) {
        pn532_ok = 1;
        PN532_SamConfiguration(&nfc);
        uart1_print("[RFID] Waiting tag...\r\n");
    } else {
        pn532_ok = 0;
        uart1_print("[RFID] PN532 NOT FOUND\r\n");
    }
}
static const char* uid_to_zone(const uint8_t* uid, uint8_t len) {
    if (len != 4) return NULL;
    if (uid[0]==0xE1 && uid[1]==0xCE && uid[2]==0x2D && uid[3]==0xFF) return "A";
    if (uid[0]==0x00 && uid[1]==0x82 && uid[2]==0xC2 && uid[3]==0x2C) return "B";
    if (uid[0]==0xB1 && uid[1]==0xE7 && uid[2]==0x32 && uid[3]==0xFF) return "C";
    if (uid[0]==0x01 && uid[1]==0xCE && uid[2]==0x2D && uid[3]==0xFF) return "D";
    return NULL;
}
static void set_zone(const char* zone) {
    if (strcmp(current_zone, zone) != 0) {
        strncpy(current_zone, zone, sizeof(current_zone) - 1);
        current_zone[sizeof(current_zone) - 1] = '\0';
        telemetry_dirty = 1;
    }
}
static void send_zone_to_phone(const char* zone) {
    uart1_print("[ZONE] ");
    uart1_print(zone);
    uart1_print("\r\n");
}
static void RFID_poll(void) {
    if (!pn532_ok) return;
    if (uid_hold_ms > 0) return;
    uint8_t uid[10];
    int uid_len = PN532_ReadPassiveTarget(&nfc, uid, PN532_MIFARE_ISO14443A, 80);
    if (uid_len <= 0) return;
    if ((uint8_t)uid_len == last_uid_len && memcmp(uid, last_uid, uid_len) == 0) {
        uid_hold_ms = 700;
        return;
    }
    memcpy(last_uid, uid, (uint8_t)uid_len);
    last_uid_len = (uint8_t)uid_len;
    const char* zone = uid_to_zone(uid, (uint8_t)uid_len);
    if (zone) {
        set_zone(zone);
        send_zone_to_phone(zone);
    } else {
        set_zone("Unknown");
        uart1_print("[ZONE] Unknown\r\n");
    }
    uid_hold_ms = 700;
}

static void send_telemetry_to_jetson(void) {
    char buf[128];
    char lbuf[12], rbuf[12];
    dtostrf(left_rpm, 4, 1, lbuf);
    dtostrf(right_rpm, 4, 1, rbuf);
    sprintf(buf,
        "STAT,mode=%s,direction=%s,rpm_l=%s,rpm_r=%s,zone=%s,age_ms=%u,speed=%d\r\n",
        is_auto_mode ? "AUTO" : "MANUAL",
        current_direction,
        lbuf,
        rbuf,
        current_zone,
        jetson_cmd_age_ms,
        manual_speed);
    uart0_print(buf);
}

static void send_status_to_bluetooth(void) {
    char buf[128];
    char lbuf[12], rbuf[12];
    dtostrf(left_rpm, 4, 1, lbuf);
    dtostrf(right_rpm, 4, 1, rbuf);
    sprintf(buf,
        "[STATUS] MODE:%s DIR:%s RPM_L:%s RPM_R:%s ZONE:%s AGE:%u SPD:%d\r\n",
        is_auto_mode ? "AUTO" : "MANUAL",
        current_direction,
        lbuf,
        rbuf,
        current_zone,
        jetson_cmd_age_ms,
        manual_speed);
    uart1_print(buf);
}

int main(void) {
    uint16_t print_timer = 0;
    uint16_t control_timer = 0;
    uint16_t rfid_timer = 0;
    static double l_f1 = 0, l_f2 = 0;
    static double r_f1 = 0, r_f2 = 0;

    motor_init();
    encoder_init();
    uart0_init_rpi();
    uart1_init_bt();
    uart1_print("SYSTEM READY\r\n");
    uart1_print("[A] AUTO, [M] MANUAL, [space] emergency stop\r\n");
    TWI_init();
    PN532_init();
    set_motor_speed(0, 0, 1, 1);
    set_current_direction("STOP");
    set_zone("-");

    while (1) {
        _delay_ms(10);
        left_no_pulse_timer += 10;
        right_no_pulse_timer += 10;
        print_timer += 10;
        control_timer += 10;
        rfid_timer += 10;
        if (is_auto_mode && jetson_cmd_age_ms < 60000) jetson_cmd_age_ms += 10;
        if (uid_hold_ms >= 10) uid_hold_ms -= 10; else uid_hold_ms = 0;

        if (left_new_pulse) {
            left_new_pulse = 0;
            uint32_t ticks;
            cli(); ticks = left_period_ticks; sei();
            if (ticks > 0) {
                double raw = (16000000.0 / (double)ticks) / PPR * 60.0;
                left_rpm = (raw + l_f1 + l_f2) / 3.0;
                l_f2 = l_f1; l_f1 = raw;
                telemetry_dirty = 1;
            }
        }
        if (left_no_pulse_timer > 200) {
            if (left_rpm != 0) telemetry_dirty = 1;
            left_rpm = 0; l_f1 = 0; l_f2 = 0;
        }

        if (right_new_pulse) {
            right_new_pulse = 0;
            uint32_t ticks;
            cli(); ticks = right_period_ticks; sei();
            if (ticks > 0) {
                double raw_r = (16000000.0 / (double)ticks) / PPR * 60.0;
                right_rpm = (raw_r + r_f1 + r_f2) / 3.0;
                r_f2 = r_f1; r_f1 = raw_r;
                telemetry_dirty = 1;
            }
        }
        if (right_no_pulse_timer > 200) {
            if (right_rpm != 0) telemetry_dirty = 1;
            right_rpm = 0; r_f1 = 0; r_f2 = 0;
        }

        if (control_timer >= 50) {
            control_timer = 0;
            if (is_auto_mode) line_follow_logic();
            else manual_control_logic();
        }
        if (rfid_timer >= 200) {
            rfid_timer = 0;
            RFID_poll();
        }
        if (print_timer >= 1000) {
            print_timer = 0;
            send_status_to_bluetooth();
            send_telemetry_to_jetson();
            telemetry_dirty = 0;
        } else if (telemetry_dirty && print_timer >= 200) {
            send_telemetry_to_jetson();
            telemetry_dirty = 0;
        }
    }
}
