import os
import re
import sys
import smtplib
from email.message import EmailMessage
import requests
from bs4 import BeautifulSoup

# 12088 er datasports id for "Rennrad 24h Rennen (4er-Team)"
BOERSE_URL = os.environ.get(
    "TICKET_URL",
    "https://www.datasport.de/anmeldeservice/314/3297/boerse/12088/",
)
TEILNEHMER_URL = "https://www.datasport.de/anmeldeservice/314/3297/teilnehmer/12088/"
OVERSIGT_URL = "https://www.datasport.de/anmeldeservice/radamring2027/"
GMAIL = os.environ.get("GMAIL_ADDRESS")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
MODTAGERE = os.environ.get("MODTAGERE")
ALARM_MODTAGER = os.environ.get("ALARM_MODTAGER")

# Seneste status: "ok" eller de sider der fejler, fx "Oversigt:layout".
# Gemmes mellem kørsler via actions/cache, så der kun sendes én alarm pr. nedbrud.
STATUS_FIL = "status.txt"
# "aaben" eller "lukket" for 4er-börsen, så alle kun får én mail når den åbner
BOERSE_FIL = "boerse.txt"

headers = {
    "User-Agent": "Mozilla/5.0 (personligt startplads-tjek)",
    "Accept-Language": "de",
}

FRIE_PLADSER = re.compile(r"freie\s+startpl.tze:?\s*(\d+)", re.IGNORECASE)
FIRER_TEAM = r"4er[\s-]*team"

FEJLTEKSTER = {
    "nede": "Siden kunne ikke hentes.",
    "omdirigeret": "Siden sender videre til en anden side. Tjek om URL'en skal opdateres.",
    "layout": (
        "Botten kunne ikke finde antallet af ledige startpladser for 4er-Team. "
        "Siden har sikkert fået nyt layout, så koden skal rettes."
    ),
}


class SideFejl(Exception):
    """maaske_plads=True betyder, at en 4er-side ser anderledes ud end normalt.
    Så får alle en mail alligevel, for en overset plads er værre end falsk alarm."""

    def __init__(self, type, detaljer="", maaske_plads=False):
        super().__init__(type)
        self.type = type
        self.detaljer = detaljer
        self.maaske_plads = maaske_plads


def send_mail(modtager, emne, indhold):
    mail = EmailMessage()
    mail["Subject"] = emne
    mail["From"] = GMAIL
    mail["To"] = modtager
    mail.set_content(indhold)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL, APP_PASSWORD)
        server.send_message(mail)


def laes_status(filnavn=STATUS_FIL, standard="ok"):
    try:
        with open(filnavn) as fil:
            return fil.read().strip() or standard
    except FileNotFoundError:
        return standard


def opdater_alarm(problemer):
    """Sender en alarm- eller 'virker igen'-mail, når status skifter."""
    status = ";".join(f"{navn}:{fejl.type}" for navn, _, fejl in problemer) or "ok"
    if status == laes_status():
        return

    if not ALARM_MODTAGER or not GMAIL or not APP_PASSWORD:
        print("Mangler ALARM_MODTAGER, GMAIL_ADDRESS eller GMAIL_APP_PASSWORD. Ingen alarm sendt.")
        return

    if not problemer:
        emne = "RAR_bot: virker igen"
        indhold = "Botten kan læse alle sider igen."
    else:
        emne = "RAR_bot: problemer med " + ", ".join(navn for navn, _, _ in problemer)
        indhold = "\n\n".join(
            f"{navn}: {FEJLTEKSTER[fejl.type]}\n{url}\n\n{fejl.detaljer}"
            for navn, url, fejl in problemer
        )

    send_mail(ALARM_MODTAGER, emne, indhold)

    # Gemmes først når mailen er sendt, så næste kørsel prøver igen hvis den fejler
    with open(STATUS_FIL, "w") as fil:
        fil.write(status)
    print(f"Alarm sendt: {emne}")


def hent_tekst(url):
    try:
        # Redirects følges ikke: en lukket side sender videre til oversigten,
        # hvor "freie Startplätze" står ved helt andre løb.
        svar = requests.get(url, headers=headers, timeout=20, allow_redirects=False)
        svar.raise_for_status()
    except requests.RequestException as fejl:
        raise SideFejl("nede", f"Fejl: {fejl}")

    if svar.is_redirect:
        raise SideFejl("omdirigeret", f"Sender videre til: {svar.headers.get('Location', 'ukendt')}")

    # svar.content i stedet for svar.text, så BeautifulSoup selv læser sidens tegnsæt
    return BeautifulSoup(svar.content, "html.parser")


def opdater_boerse(aaben):
    """Sender én mail til alle, når 4er-börsen går fra lukket til åben.
    aaben er None, hvis det ikke kunne afgøres, og så huskes den gamle status."""
    if aaben is None:
        return
    status = "aaben" if aaben else "lukket"
    if status == laes_status(BOERSE_FIL, "lukket"):
        return

    if aaben:
        if not GMAIL or not APP_PASSWORD:
            print("Mangler GMAIL_ADDRESS eller GMAIL_APP_PASSWORD. Ingen börse-mail sendt.")
            return
        send_mail(
            MODTAGERE,
            "RAD AM RING: startplatzbörsen for 4er-Team er åben!",
            f"Startplatzbörsen for 4er-Team er åben igen. Hold øje med den:\n\n{BOERSE_URL}",
        )
        print("Börse-mail sendt")

    # Gemmes først når mailen er sendt, så næste kørsel prøver igen hvis den fejler
    with open(BOERSE_FIL, "w") as fil:
        fil.write(status)


# Om 4er-börsen ser åben ud, pr. side der kan afgøre det (True/False).
# Fejler en side, mangler den her, og så ved vi det ikke.
boerse_aaben = {}

# Hver tjek-funktion returnerer kun None, når det er bekræftet at der ingen
# ledige pladser er, og ellers en kort tekst om hvad den fandt. Kan siden ikke
# læses, kastes SideFejl, med maaske_plads=True hvis det kan skyldes en ledig plads.

def tjek_overskrift(tekst, side):
    """Finder fx 'Teilnehmerliste für "Rennrad 24h Rennen (4er-Team)"'. Står der
    et andet løb, er det den forkerte side, og så sendes kun en alarm."""
    overskrift = re.search(side + r'\s+f.r\s+"([^"]*)"', tekst, re.IGNORECASE)
    if overskrift and not re.search(FIRER_TEAM, overskrift.group(1), re.IGNORECASE):
        raise SideFejl("layout", tekst[:1500])
    return overskrift is not None


def tjek_teilnehmerliste():
    tekst = hent_tekst(TEILNEHMER_URL).get_text(" ", strip=True)
    har_overskrift = tjek_overskrift(tekst, "teilnehmerliste")
    fund = FRIE_PLADSER.findall(tekst)

    if len(fund) == 1 and int(fund[0]) > 0:
        return f"freie Startplätze: {fund[0]}"
    if len(fund) == 1 and har_overskrift:
        return None
    raise SideFejl("layout", tekst[:1500], maaske_plads=True)


def er_firer_blok(blok):
    """Hvert løb har sin egen blok på oversigten. 4er-Team kendes på titlen og
    på linket til dens teilnehmerliste."""
    titel = blok.select_one(".titel")
    return (
        titel is not None
        and re.search(FIRER_TEAM, titel.get_text(), re.IGNORECASE)
        and blok.find("a", href=re.compile(r"/teilnehmer/12088/")) is not None
    )


def tjek_oversigt():
    side = hent_tekst(OVERSIGT_URL)
    blokke = [blok for blok in side.select("div.panitermine") if er_firer_blok(blok)]

    if len(blokke) != 1:
        raise SideFejl("layout", side.get_text(" ", strip=True)[:1500])

    # Åbne börser har et "Startplatzbörse ››"-link i løbets blok, ligesom de andre løb
    boerse_aaben["Oversigt"] = any(
        "/boerse/" in link.get("href", "") or re.search(r"startplatzb.rse", link.get_text(), re.IGNORECASE)
        for link in blokke[0].find_all("a")
    )

    tekst = blokke[0].get_text(" ", strip=True)
    fund = FRIE_PLADSER.findall(tekst)

    if re.search(r"jetzt\s+anmelden", tekst, re.IGNORECASE):
        return "'jetzt anmelden' er dukket op ved 4er-Team"
    if any(int(antal) > 0 for antal in fund):
        return f"freie Startplätze: {', '.join(fund)}"
    if fund == ["0"] or (not fund and re.search(r"teilnehmerlimit\s+erreicht", tekst, re.IGNORECASE)):
        return None
    raise SideFejl("layout", tekst[:1500], maaske_plads=True)


def tjek_boerse():
    try:
        tekst = hent_tekst(BOERSE_URL).get_text(" ", strip=True)
    except SideFejl as fejl:
        # Når börsen er lukket, sender datasport videre til oversigten. Det er normalt.
        if fejl.type == "omdirigeret":
            boerse_aaben["Startplatzbörse"] = False
            return None
        raise

    boerse_aaben["Startplatzbörse"] = True
    har_overskrift = tjek_overskrift(tekst, "startplatzb.rse")

    if har_overskrift and re.search(r"aktuell\s+kein\s+platz\s+verf.gbar", tekst, re.IGNORECASE):
        return None
    if har_overskrift:
        return "der er pladser i startplatzbörsen"
    raise SideFejl("layout", tekst[:1500], maaske_plads=True)


SIDER = [
    ("Teilnehmerliste", TEILNEHMER_URL, tjek_teilnehmerliste),
    ("Oversigt", OVERSIGT_URL, tjek_oversigt),
    ("Startplatzbörse", BOERSE_URL, tjek_boerse),
]

fund = []
problemer = []

for navn, url, tjek in SIDER:
    try:
        resultat = tjek()
    except SideFejl as fejl:
        print(f"{navn}: {FEJLTEKSTER[fejl.type]}")
        print(fejl.detaljer)
        problemer.append((navn, url, fejl))
        if fejl.maaske_plads:
            fund.append((navn, url, "siden ser anderledes ud end normalt, så der er måske en ledig plads. Tjek selv"))
        continue

    print(f"{navn}: {resultat or 'ingen ledige pladser'}")
    if resultat:
        fund.append((navn, url, resultat))

# Åben, hvis bare én side siger det. Lukket kun, hvis begge sider kunne læses.
if any(boerse_aaben.values()):
    opdater_boerse(True)
elif len(boerse_aaben) == 2:
    opdater_boerse(False)
else:
    opdater_boerse(None)

if fund:
    if not GMAIL or not APP_PASSWORD:
        print("Lortet virker ikke")
        sys.exit(0)

    kun_maaske = len(fund) == sum(1 for _, _, fejl in problemer if fejl.maaske_plads)
    maaske = "måske " if kun_maaske else ""
    linjer = "\n".join(f"- {navn}: {resultat}\n  {url}" for navn, url, resultat in fund)
    send_mail(
        MODTAGERE,
        f"RAD AM RING: {maaske}ledig plads på 4er-Team!",
        f"Der er {maaske}ledige startpladser på 4er-Team. Let måsen:\n\n{linjer}\n\n""ps Glade er sikkert fuld lige nu",
    )
    print("E-mail sendt")

opdater_alarm(problemer)
