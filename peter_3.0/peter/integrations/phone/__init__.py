"""Reading the phone over ADB.

Windows has no API for your handset's messages — Phone Link is closed and
there is no supported way in. The two routes that actually work are a
companion app you write and run on the phone, or ADB, which is already
installed on every developer's machine and needs no app at all.

ADB it is, and read-only: SMS in, nothing out. Sending a message as you is
both technically unreliable (it needs a default-SMS app or `service call isms`
incantations that differ per Android version) and a bad idea for something
driven by speech recognition.
"""
