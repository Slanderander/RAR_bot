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
ALARM_MODTAGER = os.environ.get("ALARM_MODTAGER")

# Seneste status ("ok", "nede" eller "layout"). Gemmes mellem kørsler via
# actions/cache, så der kun sendes én alarm pr. nedbrud.
STATUS_FIL = "status.txt"

headers = {
    "User-Agent": "Mozilla/5.0 (personligt startplads-tjek)",
    "Accept-Language": "de",
}


def send_mail(modtager, emne, indhold):
    mail = EmailMessage()
    mail["Subject"] = emne
    mail["From"] = GMAIL
    mail["To"] = modtager
    mail.set_content(indhold)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL, APP_PASSWORD)
        server.send_message(mail)


def laes_status():
    try:
        with open(STATUS_FIL) as fil:
            return fil.read().strip() or "ok"
    except FileNotFoundError:
        return "ok"


def opdater_alarm(status, detaljer=""):
    """Sender en alarm- eller 'virker igen'-mail, når status skifter."""
    if status == laes_status():
        return

    if not ALARM_MODTAGER or not GMAIL or not APP_PASSWORD:
        print("Mangler ALARM_MODTAGER, GMAIL_ADDRESS eller GMAIL_APP_PASSWORD. Ingen alarm sendt.")
        return

    if status == "ok":
        emne = "RAR_bot: virker igen"
        indhold = f"Botten kan læse siden igen.\n\n{URL}"
    elif status == "nede":
        emne = "RAR_bot: kan ikke hente siden"
        indhold = f"Botten kunne ikke hente siden.\n\n{URL}\n\n{detaljer}"
    else:
        emne = "RAR_bot: sidens layout er ændret"
        indhold = (
            "Botten kunne ikke finde antallet af ledige startpladser. "
            f"Siden har sikkert fået nyt layout, så koden skal rettes.\n\n{URL}\n\n"
            f"Sidens tekst starter med:\n\n{detaljer}"
        )

    send_mail(ALARM_MODTAGER, emne, indhold)

    # Gemmes først når mailen er sendt, så næste kørsel prøver igen hvis den fejler
    with open(STATUS_FIL, "w") as fil:
        fil.write(status)
    print(f"Alarm sendt: {emne}")


try:
    svar = requests.get(URL, headers=headers, timeout=20)
    svar.raise_for_status()
except requests.RequestException as fejl:
    print(f"Kunne ikke hente siden: {fejl}")
    opdater_alarm("nede", f"Fejl: {fejl}")
    sys.exit(1)

tekst = BeautifulSoup(svar.text, "html.parser").get_text(" ", strip=True)

fund = re.search(r"freie\s+startpl.tze:?\s*(\d+)", tekst, re.IGNORECASE)

if not fund:
    print("Kunne ikke finde antallet af startpladser. Sidens tekst starter med:")
    print(tekst[:1500])
    opdater_alarm("layout", tekst[:1500])
    sys.exit(1)

pladser = int(fund.group(1))
print(f"Ledige startpladser: {pladser}")

if pladser > 0:
    if not GMAIL or not APP_PASSWORD:
        print("Lortet virker ikke")
        sys.exit(0)

    send_mail(
        MODTAGERE,
        f"RAD AM RING: {pladser} ledige startpladser!",
        f"Der er {pladser} ledige startpladser. Let måsen:\n\n{URL}\n\n""ps Glade er sikkert fuld lige nu",
    )
    print("E-mail sendt")

opdater_alarm("ok")
