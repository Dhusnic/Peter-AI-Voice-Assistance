# imports
import speech_recognition as sr
import pyttsx3
import os
from datetime import datetime
import wikipedia
import webbrowser
from colorama import Fore
import time
import wikipedia.exceptions
import pywhatkit
import wolframalpha
#import peter2_beta
import subprocess
import psutil
from AppOpener import open
#import virtual_mouse
r = sr.Recognizer()

firefox = webbrowser.Mozilla('C:\\Program Files\\Mozilla Firefox\\firefox.exe')

def files():
    if "movies" in mytext and "folder" in mytext:
        subprocess.Popen(['explorer', "D:\\others files\\movies_series"])
        tell("Your folder movies is opend now")
        print(Fore.LIGHTGREEN_EX + "Your folder movies is opend Now")
    elif"project" in mytext and "folder" in mytext:
        subprocess.Popen(['explorer', "D:\\studies\\projects"])
        tell("Your folder project is opend Now")
        print(Fore.LIGHTGREEN_EX + "Your folder project is opend Now")
    elif"programming" in mytext and "folder" in mytext:
        subprocess.Popen(['explorer', "D:\\studies\\programing"])
        tell("Your folder programing folder is opend Now")
        print(Fore.LIGHTGREEN_EX + "Your folder programing folder is opend Now")
    elif "folder" in mytext :
        #os.system('& python c:/Users/admin/Desktop/peter.py')
        subprocess.Popen(['explorer', "D:\\"])
        tell("Your folder is opend Now")
        print(Fore.LIGHTGREEN_EX + "Your folder is opend Now")
def ear():
    r = sr.Recognizer()
    with sr.Microphone() as source2:
        r.adjust_for_ambient_noise(source2, duration=0.1)
        r.pause_threshold = 2
        audio2 = r.listen(source2)
        try:
            mytext = r.recognize_google(audio2)
            (mytext) = mytext.lower()
            print(Fore.LIGHTBLUE_EX + mytext)
        except  sr.RequestError as e:
            print("could not request results;{0}".format(e))
        except sr.UnknownValueError:
            print(Fore.BLUE + "cant understand please tell again ")
            tell("I cant understand please tell again")

def listen():
     with sr.Microphone() as source2:
                r = sr.Recognizer()
                r.adjust_for_ambient_noise(source2, duration=0.3)
                r.pause_threshold = 1
                audio2 = r.listen(source2)
                global mytext
                mytext = r.recognize_google(audio2)
                mytext = mytext.lower()
                print(mytext)
                return mytext
# function to pause a program
def pause():
    print(Fore.LIGHTRED_EX + "alright,wake me up when you want my help,by pressing enter")
    tell("alright,wake me up when you want my help , by pressinng enter")
    os.system('pause')


# function to make program speak
def tell(text):
    engine = pyttsx3.init('sapi5')
    engine.say(text)
    engine.runAndWait()
    engine.setProperty('rate', 100)
    engine.setProperty('volume', 1.0)
    voices = engine.getProperty('voices')
    engine.setProperty('voice', voices[1].id)


# function make a wish
def wishme():
    hr = datetime.now().hour
    if hr >= 0 and hr < 12:
        tell("Hello Dhusnic,i am peter,Good Morning,what can i do for you ")
        print("Hello Dhusnic,i am peter,Good Morning,what can i do for you ")
    if hr >= 12 and hr < 18:
        tell("Hello Dhusnic,i am peter,Good Afternoon,what can i do for you  ")
        print("Hello Dhusnic,i am peter,Good Afternoon,what can i do for you")
    if hr > 18:
        tell("Hello Dhusnic,i am peter,Good evening,what can i do for you  ")
        print("Hello Dhusnic,i am peter,Good evening,what can i do for you ")



try:
    # loop of inactive a ai
    while True:
        try:
            tell("Welcome dhusnic i am peter,call me when you need me")
            print("Welcome dhusnic i am peter,call me when you need me")
            listen()
            # loop of an active ai
            if "peter" in mytext or "listen" in mytext or "pete" in mytext:
                os.system('cls')
                print(Fore.LIGHTBLUE_EX + "listening.........")
                wishme()
                print(Fore.LIGHTGREEN_EX + "Hi dhusnic ,", Fore.MAGENTA + " I am peter,",
                          Fore.YELLOW + "What can i do for you")
                    # loop to have questions
                while True:
                    try:
                        # with sr.Microphone() as source2:
                        #     r.adjust_for_ambient_noise(source2)
                        #     audio2 = r.listen(source2)
                        #     mytext = r.recognize_google(audio2)
                        #     (mytext) = mytext.lower()
                        #     print(Fore.LIGHTBLUE_EX + mytext)
                            listen()
                            # call peter
                            if "peter" in mytext:
                                wishme()

                            # tell date
                            if "date" in mytext:
                                dt = datetime.today().strftime("%d-%b-%Y")
                                print(Fore.YELLOW + "Todays Date is" + dt)
                                tell("Todays Date  is   " + dt)

                            # tell time
                            if "time" in mytext:
                                ct = datetime.today().strftime("%I:%M %p")
                                print(Fore.MAGENTA + "The time now is   " + ct)
                                tell("The time now is   " + ct)

                            # make a program sleep
                            if "sleep" in mytext or "bye" in mytext or "leave" in mytext or "stay" in mytext or "quiet"in mytext or "wait" in mytext:
                                print(Fore.LIGHTRED_EX + "ok bye ,see you later")
                                tell("ok bye ,see you later")
                                break

                            # clear the screen
                            if "clear" in mytext:
                                os.system('cls')

                            # search in wikipedia
                            if "wikipedia" in mytext:
                                try:
                                    tell("searching in wikipedia")
                                    mytext = mytext.replace("wikipedia search" or "wikipedia", "")
                                    wikiqus=mytext
                                    results = wikipedia.summary(mytext, sentences=3)
                                    print(Fore.LIGHTRED_EX + results)
                                    tell(results)
                                    print("Do you need more Detial about",mytext,"?")
                                    tell("Do you need more detials about")
                                    tell(wikiqus)
                                    try:
                                        listen()
                                        if "yes" in mytext:
                                            results = wikipedia.summary(wikiqus, sentences=10)
                                            print(Fore.LIGHTRED_EX + results)
                                            tell(results)
                                    except:
                                        tell("ok thank you for visiting wikipedia")
                                        pass
                                    
                                except wikipedia.exceptions.PageError as e:
                                    print("try it in another way")
                                    tell("try it in another way")
                                except wikipedia.exceptions.DisambiguationError as e:
                                    print("try it in another way")
                                    tell("try it in another way")

                            # open a youtube tab
                            if 'open youtube' in mytext:
                                webbrowser.open_new_tab("https://www.youtube.com")
                                tell("youtube is open now")
                                print(Fore.RED + "youtube is open now")
                                time.sleep(5)

                            # open a google tab
                            if 'open google' in mytext :
                                webbrowser.open_new_tab("https://www.google.com")
                                print(Fore.LIGHTGREEN_EX + "browser is open now")
                                tell("browser is open now")
                                time.sleep(5)

                            # open a gmail
                            if 'open gmail' in mytext or 'open my gmail' in mytext:
                                firefox.open(" https://mail.google.com/mail/u/0/#inbox ")
                                tell("Google Mail open now")
                                print(Fore.MAGENTA + "Google Mail open now")
                                time.sleep(5)

                            # open my youtube channel
                            if ' my youtube channel' in mytext or "just belive" in mytext or "my yt channel" in mytext:
                                webbrowser.open_new_tab("https://www.youtube.com/channel/UC2r-qtZPbbBJri1EXlQ2Q0g")
                                tell("your youtube channel is  open now")
                                print(Fore.BLUE + "your youtube channel is  open now")
                                time.sleep(5)

                            # open my instagram
                            if ' instagram' in mytext:
                                webbrowser.open_new_tab("https://www.instagram.com/")
                                tell("your Instagram is  open now")
                                print(Fore.BLUE + "your Instagram is  open now")
                                time.sleep(5)

                            # open my online class
                            if ' online class' in mytext or 'class' in mytext:
                                webbrowser.open_new_tab(" https://meet.google.com/exv-cmyj-oiv")
                                tell("your google class is  open now")
                                print(Fore.BLUE + "your google class is  open now")
                                time.sleep(5)

                            #portfolio
                            if ' portfolio' in mytext or 'trading account' in mytext:
                                webbrowser.open_new_tab("https://kite.zerodha.com/dashboard")
                                tell("your portfolio is open now")
                                print(Fore.BLUE + "your  portfolio is  open now")
                                time.sleep(5)

                            #trading charts
                            if 'trading view' in mytext or 'trading charts' in mytext:
                                webbrowser.open_new_tab(" https://in.tradingview.com/chart/lYrkMfcS/")
                                tell("your trading view is open now")
                                print(Fore.BLUE + " your trading view is open now")
                                time.sleep(5)


                            # open my personal drive
                            if ' personal drive' in mytext or 'my drive' in mytext:
                                webbrowser.open_new_tab("https://drive.google.com/drive/u/0/quota ")
                                tell("your personal drive is  open now")
                                print(Fore.BLUE + "your personal drive is  open now")
                                time.sleep(5)

                            # open my study drive
                            if ' study drive' in mytext or 'projects' in mytext:
                                webbrowser.open_new_tab("https://drive.google.com/drive/u/1/my-drive")
                                tell("your study drive is  open now")
                                print(Fore.BLUE + "your study drive is  open now")
                                time.sleep(5)

                            # open my github
                            if ' github' in mytext or 'my github' in mytext:
                                webbrowser.open_new_tab(
                                    "https://github.com/SpectralOps/keyscope?utm_term=github&utm_campaign=GitHub_Keyscope&utm_source=google&utm_medium=ppc&hsa_acc=1287660619&hsa_cam=14863556156&hsa_grp=127226304639&hsa_ad=550063928531&hsa_src=g&hsa_tgt=kwd-11648088761&hsa_kw=github&hsa_mt=e&hsa_net=adwords&hsa_ver=3&gclid=Cj0KCQjww4OMBhCUARIsAILndv4xt9yMfZKGWtoBLHWZODV-KMVNi_te9f3_CnArEY_ZTCYddCGhhIsaAgyREALw_wcB")
                                tell("your github is  open now")
                                print(Fore.BLUE + "your github is  open now")
                                time.sleep(5)

                            # open  study gmail
                            if 'study gmail' in mytext or 'college mail' in mytext:
                                webbrowser.open_new_tab("https://mail.google.com/mail/u/1/#inbox")
                                tell("Google Mail of studies open now")
                                print(Fore.MAGENTA + "Google Mail  of studies open now")
                                time.sleep(5)

                            # open whatsapp
                            if 'open whatsapp' in mytext:
                                webbrowser.open_new_tab("https://web.whatsapp.com/")
                                tell("whatsapp is open now")
                                print(Fore.RED + "whatsapp is open now")
                                time.sleep(5)

                            # # pause a program
                            # if "pause" in mytext or "wait" in mytext or "sleep" in mytext:
                            #     tell('ok ,i am shutting down')
                            #     print(Fore.LIGHTGREEN_EX + 'ok ,i am shutting down')
                            #     break

                            # open  a browser
                            if "browser" in mytext or "firefox" in mytext:
                                open("firefox")
                                tell("your fire fox browser is opening")
                                print(Fore.LIGHTRED_EX + "your fire fox browser is opening")

                            # paly videos in youtube
                            if "play" in mytext and "youtube" in mytext:
                                try:
                                    mytext = mytext.replace("play" and "youtube", "")
                                    pywhatkit.playonyt(mytext)
                                    print(Fore.LIGHTRED_EX + " playing ", mytext, "youtube")
                                    tell(" playing " + mytext + "youtube")
                                    break
                                except:
                                    pass

                            # search and open tab in gooogle
                            if "search" in mytext and "google" in mytext and "show" in mytext:
                                try:
                                    mytext = mytext.replace("google", "")
                                    mytext = mytext.replace("search", "")
                                    mytext = mytext.replace("searching", "")
                                    mytext = mytext.replace("show", "")
                                    mytext = mytext.replace("and", "")

                                    pywhatkit.search(mytext)
                                    print(Fore.LIGHTRED_EX + " searching ", mytext, "google")
                                    tell(" searching " + mytext + "google")
                                except:
                                    pass

                            # search and tell in google
                            if "search" in mytext and "google" in mytext :
                                try:
                                    mytext = mytext.replace("google", "")
                                    mytext = mytext.replace("search", "")
                                    mytext = mytext.replace("searching", "")
                                    mytext = mytext.replace("show", "")
                                    mytext = mytext.replace("tell", "")
                                    gt = pywhatkit.info(mytext, lines=3)
                                    tell(" searching " + mytext + "google")
                                    tell(pywhatkit.info(mytext, lines=3))
                                except:
                                    pass

                            # to close a program
                            if "log out" in mytext or "logout" in mytext or "leave" in mytext:
                                tell('ok ,i am shutting down')
                                print(Fore.LIGHTGREEN_EX + 'ok ,i am shutting down')
                                exit()

                            # ask answer in wolf
                            if 'ask to wolf' in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + 'I can answer to you,what is the question')
                                    tell('I can answer to you,what is the question')
                                    ear()
                                    mytext = mytext.replace("ask to wolf", "")
                                    app_id = "4G8HAV-P2A7KT99GE "
                                    client = wolframalpha.Client('R2K75H-7ELALHR35X')
                                    res = client.query(mytext)
                                    answer = next(res.results).text
                                    tell(answer)
                                    print(answer)
                                except:
                                    tell("try it in another way")
                                    pass
                            # restart a machine
                            if "restart yourself" in mytext:
                                #os.system('& python c:/Users/admin/Desktop/peter.py')
                                exec(open('peter2_beta.py').read())
                                exit()
                            #open solid works
                            if "solid works" in mytext or "solidworks" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your solid work 3D modeling is opening")
                                    tell("your solid work 3D modeling is opening")
                                    # os.chdir('C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS')
                                    # os.system('SLDWORKS.exe')
                                    subprocess.Popen('C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe')
                                except:
                                    pass
                            #open autocad
                            if "autocad" in mytext or "auto cad" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your Autocad 2D modeling is opening")
                                    tell("your autocad 2D modeling is opening")
                                    # os.chdir('C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS')
                                    # os.system('SLDWORKS.exe')
                                    subprocess.Popen('C:\\Program Files\\Autodesk\\AutoCAD 2022\\acad.exe')

                                except:
                                    pass

                            #open ardiuno Ide
                            if "ardiuno" in mytext or "ardiuno ide" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your Autocad 2D modeling is opening")
                                    tell("your autocad 2D modeling is opening")
                                    # os.chdir('C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS')
                                    # os.system('SLDWORKS.exe')
                                    subprocess.Popen('C:\\Program Files\\Arduino IDE\\Arduino IDE.exe')

                                except:
                                    pass
                            #open VMware
                            if "vmware" in mytext or "virtual enviroinment" in mytext or "virtual machine" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your  VMware is opening")
                                    tell("your  VMware is opening")
                                    # os.chdir('C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS')
                                    # os.system('SLDWORKS.exe')
                                    subprocess.Popen('C:\\Program Files (x86)\\VMware\\VMware Player\\vmplayer.exe')

                                except:
                                    pass
                            #open near byshare
                            if "near by share" in mytext or "share files" in mytext or "share" in mytext or "files" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your near by share is opening")
                                    tell("your near by share is opening")
                                    # os.chdir('C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS')
                                    # os.system('SLDWORKS.exe')
                                    subprocess.Popen('C:\\Program Files\\Google\\NearbyShare\\nearby_share.exe')

                                except:
                                    pass
                            #open chatGPT
                            if 'open chat gpt' in mytext or 'chat gpt' in mytext :
                                webbrowser.open_new_tab("https://chat.openai.com/")
                                print(Fore.LIGHTGREEN_EX + "ChatGPT is open now")
                                tell("chatgpt is open now")
                                time.sleep(5)
                            #open chatGPT
                            if 'open anime' in mytext or 'naruto' in mytext or 'zoro' in mytext or 'zoro.com' in mytext :
                                webbrowser.open_new_tab("https://sanji.to/watch/naruto-shippuden-355?ep=7927")
                                print(Fore.LIGHTGREEN_EX + "Naruto is open now")
                                tell("Naruto is open now")
                                time.sleep(5)
                            #open vs Code
                            if "vs code" in mytext or "visual code" in mytext or "visual studio" in mytext or "code editor" in mytext:
                                try:
                                    print(Fore.LIGHTRED_EX + "your  VS code is opening")
                                    tell("your Visual code is opening")
                                    subprocess.Popen('C:\\Users\\DHUSNIC INFANT DM\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe')
                                except:
                                    pass
                            
                             #open chatGPT
                            
                            #spread sheet
                            if 'spread sheet' in mytext or 'spread sheets' in mytext or 'sheets' in mytext :
                                webbrowser.open_new_tab("https://docs.google.com/spreadsheets/u/0/?tg")
                                print(Fore.LIGHTGREEN_EX + "spread sheet is open now")
                                tell("spread sheet is open now")
                                time.sleep(5)

                            #open hand gusters
                            if "open hand gesture" in mytext or 'open gesture' in mytext or 'open virtual mouse' in mytext:
                                #os.system('& python c:/Users/admin/Desktop/peter.py')
                                tell("now your use hand to navigate mouse")
                                print(Fore.LIGHTGREEN_EX + "now use your hand to navigate mouse")
                                #os.system('python virtual_mouse.py')
                                virtual_mouse=subprocess.Popen(['python','D:\\studies\\programing\\python projects\\virtual_mouse_2.0.py'])
                                listen()
                            #exit hand gestuer
                            if "exit hand gesture" in mytext or 'exit gesture' in mytext or 'exit virtual mouse' in mytext or 'close virtual mouse' in mytext :
                                pid=0
                                pid=virtual_mouse.pid
                                if pid:
                                    # Close the running Python file
                                    tell("Virtual mouse is shutting down")
                                    print(Fore.LIGHTGREEN_EX + "Virtual mouse is shutting down")
                                    psutil.Process(pid).terminate()
                                else:
                                    print("No running virtual mouse found.")
                                    tell("No running virtual mouse found.")
                            files()
                            

                                    
                            







                    except  sr.RequestError as e:
                        print("could not request results;{0}".format(e))
                    except sr.UnknownValueError:
                        print(Fore.BLUE + "cant understand please tell again ")
                        tell("cant understand please tell again")

        except sr.RequestError as e:
            pass
        except sr.UnknownValueError:
            pass
except:
    pass
