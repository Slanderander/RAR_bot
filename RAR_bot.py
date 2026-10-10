import os
import re
import sys
import smtplib
from email.message import EmailMessage
import requests
from bs4 import BeautifulSoup

URL = os.environ.get(
    "TICKET_URL",
    "https://www.datasport.de/anmeldeservice/314/3297/boerse/12088/",
)
GMAIL = os.environ.get("GMAIL_ADDRESS")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
MODTAGERE = os.environ.get("MODTAGERE")

headers = {
    "User-Agent": "Mozilla/5.0 (personligt startplads-tjek)",
    "Accept-Language": "de",
}

try:
    svar = requests.get(URL, headers=headers, timeout=20)
    svar.raise_for_status()
except requests.RequestException as fejl:
    print(f"Kunne ikke hente siden: {fejl}")
    sys.exit(0)
    
tekst = BeautifulSoup(svar.text, "html.parser").get_text(" ", strip=True)

fund = re.search(r"freie\s+startpl.tze:?\s*(\d+)", tekst, re.IGNORECASE)

if not fund:
    print("Kunne ikke finde antallet af startpladser. Sidens tekst starter med:")
    print(tekst[:1500])
    sys.exit(0)

pladser = int(fund.group(1))
print(f"Ledige startpladser: {pladser}")

if pladser >= 0:
    if not GMAIL or not APP_PASSWORD:
        print("Der er pladser! (Gmail er ikke sat op, så ingen e-mail sendt)")
        sys.exit(0)

    mail = EmailMessage()
    mail["Subject"] = f"RAD AM RING: {pladser} ledige startplads(er)!"
    mail["From"] = GMAIL
    mail["To"] = MODTAGERE
    mail.set_content(f"Der er {pladser} ledige startpladser. Skynd dig:\n\n{URL}")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL, APP_PASSWORD)
        server.send_message(mail)
    print("E-mail sendt")