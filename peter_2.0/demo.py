from datetime import datetime
import pyttsx3

def tell(text):
    engine = pyttsx3.init('sapi5')
    engine.say(text)
    engine.runAndWait()
    engine.setProperty('rate', 100)
    engine.setProperty('volume', 1.0)
    voices = engine.getProperty('voices')
    engine.setProperty('voice', voices[1].id)
dt = datetime.today().strftime("%d-%b-%Y")
print("Todays Date is" + dt)
tell(dt)

ct = datetime.today().strftime("%I:%M %p")
print("The time now is   " + ct)
tell(ct)