
#include <HX711_ADC.h>
#include <Wire.h>
#include <hd44780.h>
#include <hd44780ioClass/hd44780_I2Cexp.h>
#include <Servo.h>

// Pini pt motoare
const int pulse1 = 6;
const int dir1 = 5;

const int pulse2 = 7;
const int dir2 = 8;

// Pulsuri pt motoare (sus jos)
const int CAMERA_PULSE = pulse1;
const int CAMERA_DIR = dir1;

// Pulsuri pt motoare (Fata spate)
const int SCALE_PULSE = pulse2;
const int SCALE_DIR = dir2;

// Viteza motoarelor
int stepDelay = 400;

const int CAMERA_STEPS = 1200;
const int SCALE_STEPS = 1200;
const int HOME_STEPS = 1600;

const float Z_STEPS_PER_MM = 240.0;

const int CAMERA_UP_DIR = LOW;
const int CAMERA_DOWN_DIR = HIGH;
const int SCALE_FRONT_DIR = LOW;
const int SCALE_BACK_DIR = HIGH;

// Alti pini
#define HX711_DOUT 25
#define HX711_SCK  26
#define RL_PIN 4

#define CR_SERVO_PIN 32
#define CR_SIGNAL_PIN 34

// CRtouch (NETERMINAT)
const int CR_DEPLOY_ANGLE = 10;
const int CR_STOW_ANGLE = 90;

const unsigned long CR_TRIGGER_DEBOUNCE_MS = 10;

const bool AUTO_RETRACT_AFTER_TOUCH = false;

const float MAX_PROBE_MM = 35.0;
const float RETRACT_AFTER_TOUCH_MM = 3.0;
const float SECOND_PROBE_RETRACT_MM = 1.0;

const int PROBE_FAST_DELAY = 900;
const int PROBE_SLOW_DELAY = 4000;

// Senzori
HX711_ADC LoadCell(HX711_DOUT, HX711_SCK);
hd44780_I2Cexp lcd;
Servo crServo;

// Alte variabile
float bedReferenceMm = 0;
float touchMm = 0;
float thickness = 0;
float weight = 0;
float diameter = 0;

bool bedReferenceReady = false;
bool measurementInProgress = false;
bool probeStopLatched = false;
int crIdleState = HIGH;

long zPositionSteps = 0;
unsigned long lastSerialTime = 0;

void processSerialCommand(char cmd);
void updateLCD(String line1, String line2 = "", String line3 = "", String line4 = "");
void sendDataToPython();

bool isCRTouchTriggered();
void learnCRTouchIdleState();
void printCRTouchState();
void latchProbeStop(const char *source);
void deployCRTouch();
void stowCRTouch();

bool moveStepper(int pulsePin, int dirPin, int direction, long steps, int delayUs, bool stopOnProbe);
bool delayWithProbeCheck(int pulsePin, int delayUs, bool stopOnProbe);
int zDirectionSign(int direction);

void homeSequence();
void cameraUp();
void cameraDown();
void scaleFront();
void scaleBack();
void ledsOn();
void ledsOff();

void electronicMeasure();
void performMeasurement();
float probeWithCRTouch();

float measureWeight();
float measureDiameter();

void setup() {
  pinMode(pulse1, OUTPUT);
  pinMode(dir1, OUTPUT);
  pinMode(pulse2, OUTPUT);
  pinMode(dir2, OUTPUT);

  digitalWrite(pulse1, LOW);
  digitalWrite(pulse2, LOW);
  digitalWrite(dir1, LOW);
  digitalWrite(dir2, LOW);

  pinMode(RL_PIN, OUTPUT);
  digitalWrite(RL_PIN, HIGH);

  Serial.begin(115200);
  Wire.begin();

  pinMode(CR_SIGNAL_PIN, INPUT_PULLUP);

  int lcdStatus = lcd.begin(20, 4);
  if (lcdStatus) {
    while (1);
  }

  lcd.backlight();
  lcd.clear();

  LoadCell.begin();
  LoadCell.start(2000, true);
  LoadCell.setCalFactor(696.0);
  LoadCell.tare();

  crServo.attach(CR_SERVO_PIN);
  stowCRTouch();
  learnCRTouchIdleState();

  updateLCD("Sistem pregatit", "Control Python");
  Serial.println("<READY>");
}

void loop() {
  if (Serial.available()) {
    char cmd = Serial.read();

    if (cmd != '\n' && cmd != '\r') {
      processSerialCommand(cmd);
    }

    lastSerialTime = millis();
  }

  if (!measurementInProgress) {
    LoadCell.update();
  }

  if (millis() - lastSerialTime > 5000 && !measurementInProgress) {
    Serial.println("<ALIVE>");
    lastSerialTime = millis();
  }

  delay(5);
}

void processSerialCommand(char cmd) {
  if (probeStopLatched && cmd != 'R' && cmd != 'Q' && cmd != 'T') {
    Serial.println("<LOCKED_PROBE_STOP_SEND_R>");
    return;
  }

  switch (cmd) {
    case 'R':
      probeStopLatched = false;
      updateLCD("STOP resetat", "Verifica directia");
      Serial.println("<PROBE_STOP_RESET>");
      break;

    case 'T':
      printCRTouchState();
      break;

    case 'H': homeSequence(); break;
    case 'S': cameraUp(); break;
    case 'J': cameraDown(); break;
    case 'F': scaleFront(); break;
    case 'X': scaleBack(); break;
    case 'Z': ledsOn(); break;
    case 'Y': ledsOff(); break;
    case 'M': electronicMeasure(); break;
    case 'Q': Serial.println("Conectat"); break;

    case 'P':
      ledsOn();
      delay(500);
      Serial.println("CAPTURE");
      break;

    case 'O':
      ledsOff();
      break;

    default:
      Serial.println("<UNKNOWN>");
      break;
  }
}

// Motoare (miscare)
bool moveStepper(int pulsePin, int dirPin, int direction, long steps, int delayUs, bool stopOnProbe) {
  digitalWrite(dirPin, direction);
  delayMicroseconds(80);

  for (long i = 0; i < steps; i++) {
    if (stopOnProbe && isCRTouchTriggered()) {
      digitalWrite(pulsePin, LOW);
      latchProbeStop("before_step");
      Serial.println("<PROBE_STOP_BEFORE_STEP>");
      return true;
    }

    digitalWrite(pulsePin, HIGH);

    if (delayWithProbeCheck(pulsePin, delayUs, stopOnProbe)) {
      Serial.println("<PROBE_STOP_DURING_HIGH>");
      return true;
    }

    digitalWrite(pulsePin, LOW);

    if (pulsePin == CAMERA_PULSE) {
      zPositionSteps += zDirectionSign(direction);
    }

    if (delayWithProbeCheck(pulsePin, delayUs, stopOnProbe)) {
      Serial.println("<PROBE_STOP_DURING_LOW>");
      return true;
    }
  }

  return false;
}

bool delayWithProbeCheck(int pulsePin, int delayUs, bool stopOnProbe) {
  const int CHECK_CHUNK_US = 25;
  int elapsed = 0;

  while (elapsed < delayUs) {
    if (stopOnProbe && isCRTouchTriggered()) {
      digitalWrite(pulsePin, LOW);
      latchProbeStop("pulse_delay");
      return true;
    }

    delayMicroseconds(CHECK_CHUNK_US);
    elapsed += CHECK_CHUNK_US;
  }

  return false;
}

int zDirectionSign(int direction) {
  return direction == CAMERA_DOWN_DIR ? 1 : -1;
}

void homeSequence() {
  updateLCD("Homing Z...", "CR Touch");
  Serial.println("<HOME_START>");

  float zHome = probeWithCRTouch();

  if (zHome < 0) {
    if (probeStopLatched) {
      updateLCD("STOP CR Touch", "Trimite R");
      Serial.println("<HOME_STOPPED_PROBE>");
    } else {
      updateLCD("Homing esuat", "CR Touch fail");
      Serial.println("<HOME_FAIL>");
    }
    return;
  }

  zPositionSteps = 0;
  bedReferenceMm = 0;
  bedReferenceReady = true;
  thickness = 0;

  updateLCD("Homing Z gata", "Referinta pat OK");
  Serial.println("<HOME_Z_DONE>");
  Serial.println("<HOME_DONE>");
}

void cameraUp() {
  updateLCD("Camera sus");
  Serial.println("<CAMERA_UP_START>");

  moveStepper(CAMERA_PULSE, CAMERA_DIR, CAMERA_UP_DIR, CAMERA_STEPS, stepDelay, false);

  Serial.println("<CAMERA_UP_DONE>");
}

void cameraDown() {
  updateLCD("Camera jos");
  Serial.println("<CAMERA_DOWN_START>");

  bool stoppedByProbe = moveStepper(
    CAMERA_PULSE,
    CAMERA_DIR,
    CAMERA_DOWN_DIR,
    CAMERA_STEPS,
    stepDelay,
    true
  );

  if (stoppedByProbe) {
    updateLCD("Camera oprita", "Probe apasat");
    Serial.println("<CAMERA_DOWN_STOP_PROBE>");
  } else {
    Serial.println("<CAMERA_DOWN_DONE>");
  }
}

void scaleFront() {
  updateLCD("Cantar fata");
  Serial.println("<SCALE_FRONT_START>");

  moveStepper(SCALE_PULSE, SCALE_DIR, SCALE_FRONT_DIR, SCALE_STEPS, stepDelay, false);

  Serial.println("<SCALE_FRONT_DONE>");
}

void scaleBack() {
  updateLCD("Cantar spate");
  Serial.println("<SCALE_BACK_START>");

  moveStepper(SCALE_PULSE, SCALE_DIR, SCALE_BACK_DIR, SCALE_STEPS, stepDelay, false);

  Serial.println("<SCALE_BACK_DONE>");
}

// Program pt LEDuri
void ledsOn() {
  digitalWrite(RL_PIN, LOW);
  updateLCD("LED-uri ON");
  Serial.println("<LED_ON>");
}

void ledsOff() {
  digitalWrite(RL_PIN, HIGH);
  updateLCD("LED-uri OFF");
  Serial.println("<LED_OFF>");
}

// Control CRtouch (NETERMINAT)
void deployCRTouch() {
  crServo.write(CR_DEPLOY_ANGLE);
  delay(600);
}

void stowCRTouch() {
  crServo.write(CR_STOW_ANGLE);
  delay(600);
}

bool isCRTouchTriggered() {
  if (digitalRead(CR_SIGNAL_PIN) == crIdleState) {
    return false;
  }

  unsigned long startedAt = millis();
  while (millis() - startedAt < CR_TRIGGER_DEBOUNCE_MS) {
    if (digitalRead(CR_SIGNAL_PIN) == crIdleState) {
      return false;
    }
    delayMicroseconds(100);
  }

  return true;
}

void learnCRTouchIdleState() {
  crIdleState = digitalRead(CR_SIGNAL_PIN);
  Serial.print("<CR_IDLE,");
  Serial.print(crIdleState);
  Serial.println(">");
}

void printCRTouchState() {
  int rawState = digitalRead(CR_SIGNAL_PIN);

  Serial.print("<CR_RAW,");
  Serial.print(rawState);
  Serial.print(",IDLE,");
  Serial.print(crIdleState);
  Serial.print(",TRIGGERED,");
  Serial.print(isCRTouchTriggered() ? 1 : 0);
  Serial.println(">");
}

void latchProbeStop(const char *source) {
  probeStopLatched = true;
  digitalWrite(CAMERA_PULSE, LOW);
  digitalWrite(SCALE_PULSE, LOW);

  Serial.print("<PROBE_STOP_LATCHED,");
  Serial.print(source);
  Serial.println(">");
}

float probeWithCRTouch() {
  probeStopLatched = false;
  deployCRTouch();
  learnCRTouchIdleState();

  long maxProbeSteps = (long)(MAX_PROBE_MM * Z_STEPS_PER_MM);

  updateLCD("CR Touch", "Coboara...");
  Serial.println("<PROBE_START>");

  bool touched = moveStepper(
    CAMERA_PULSE,
    CAMERA_DIR,
    CAMERA_DOWN_DIR,
    maxProbeSteps,
    PROBE_SLOW_DELAY,
    true
  );

  long touchSteps = zPositionSteps;

  if (touched) {
    if (AUTO_RETRACT_AFTER_TOUCH) {
      updateLCD("CR Touch", "Atins", "Retragere...");
    } else {
      updateLCD("CR Touch atins", "STOP - trimite R");
      Serial.println("<PROBE_TRIGGERED_STOPPED>");
    }
    Serial.println("<PROBE_TRIGGERED>");
  } else {
    updateLCD("CR Touch", "Nu a atins");
    Serial.println("<PROBE_FAIL>");
  }

  stowCRTouch();

  if (touched && AUTO_RETRACT_AFTER_TOUCH) {
    long retractSteps = (long)(RETRACT_AFTER_TOUCH_MM * Z_STEPS_PER_MM);
    moveStepper(CAMERA_PULSE, CAMERA_DIR, CAMERA_UP_DIR, retractSteps, stepDelay, false);
  }

  if (!touched) {
    return -1;
  }

  float zMm = touchSteps / Z_STEPS_PER_MM;

  Serial.print("<CRTOUCH_Z,");
  Serial.print(zMm, 3);
  Serial.println(">");

  if (!AUTO_RETRACT_AFTER_TOUCH) {
    return -1;
  }

  return zMm;
}

// Masurare
void electronicMeasure() {
  if (!measurementInProgress) {
    performMeasurement();
  } else {
    Serial.println("<BUSY>");
  }
}

void performMeasurement() {
  measurementInProgress = true;

  updateLCD("Masurare...", "CR Touch activ");
  Serial.println("<MEASURE_START>");

  weight = measureWeight();
  diameter = measureDiameter();

  touchMm = probeWithCRTouch();

  if (touchMm < 0) {
    thickness = 0;
    if (probeStopLatched) {
      updateLCD("STOP CR Touch", "Trimite R");
      Serial.println("<MEASURE_STOPPED_PROBE>");
      measurementInProgress = false;
      Serial.println("<MEASURE_ABORTED_PROBE>");
      return;
    } else {
      updateLCD("Eroare CR Touch", "Nu a atins", "Verifica probe");
      Serial.println("<MEASURE_FAIL>");
    }
  } else if (!bedReferenceReady) {
    bedReferenceMm = touchMm;
    bedReferenceReady = true;
    thickness = 0;

    updateLCD(
      "Referinta salvata",
      "Pat masurat",
      "Apasa M cu moneda"
    );

    Serial.print("<BED_REF,");
    Serial.print(bedReferenceMm, 3);
    Serial.println(">");
  } else {
    thickness = bedReferenceMm - touchMm;
    if (thickness < 0) {
      thickness = -thickness;
    }

    updateLCD(
      "Rezultat masurare",
      "Grosime: " + String(thickness, 2) + " mm",
      "Greutate: " + String(weight, 2) + " g",
      "Diametru: " + String(diameter, 1) + " mm"
    );

    Serial.print("<THICKNESS,");
    Serial.print(thickness, 2);
    Serial.println(">");
  }

  sendDataToPython();

  measurementInProgress = false;
  Serial.println("<MEASURE_DONE>");
}

float measureWeight() {
  LoadCell.update();
  return LoadCell.getData();
}

float measureDiameter() {
  return 0;
}

void sendDataToPython() {
  Serial.print("<M,");
  Serial.print(thickness, 2);
  Serial.print(",");
  Serial.print(diameter, 1);
  Serial.println(">");

  Serial.print("<W,");
  Serial.print(weight, 2);
  Serial.println(">");

  if (thickness >= 1.0 && thickness <= 4.0) {
    Serial.println("<GREEN>");
  } else {
    Serial.println("<RED>");
  }

  Serial.println("<DATA_SENT>");
}

// LCD
void updateLCD(String line1, String line2, String line3, String line4) {
  lcd.clear();

  lcd.setCursor(0, 0);
  lcd.print(line1);

  if (line2.length() > 0) {
    lcd.setCursor(0, 1);
    lcd.print(line2);
  }

  if (line3.length() > 0) {
    lcd.setCursor(0, 2);
    lcd.print(line3);
  }

  if (line4.length() > 0) {
    lcd.setCursor(0, 3);
    lcd.print(line4);
  }
}
