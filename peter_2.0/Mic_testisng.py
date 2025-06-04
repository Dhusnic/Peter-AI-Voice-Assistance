import speech_recognition as sr
mic_list = sr.Microphone.list_microphone_names()
# print(mic_list)

for i in range(len(mic_list)):
    # print(i, mic_list[i])
    recognizer = sr.Recognizer()
    mic_index = 1  # <-- change based on your system
    microphone = sr.Microphone(device_index=mic_index)
    try:
        with microphone as source:
            # print("🎙️ Say something...")
            recognizer.adjust_for_ambient_noise(source)
            audio = recognizer.listen(source)
            print("🎙️ Done listening...",mic_list,i)
        try:
            print("🧠 Recognizing...")
            text = recognizer.recognize_google(audio)
            print(f"✅ You said: {text}")
        except sr.UnknownValueError:
            print("❌ Could not understand audio")
        except sr.RequestError:
            print("❌ Could not request results from Google Speech Recognition service")
    except Exception as e:
        continue
    
