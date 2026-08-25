# Analizator Rynkowy

Program pomaga w wyszukiwaniu okazji rynkowych na gieldzie. Skanuje akcje, indeksy i ETF-y, analizuje je i pokazuje, ktore z nich wygladaja na niedowartosciowane lub maja dobra pozycje do zakupu.

## Do czego sluzy ten program

- Wyszukuje okazje rynkowe na podstawie wskaznikow technicznych (np. RSI, srednie kroczace).
- Ocenia, czy spolka jest niedowartosciowana (Value Investing).
- Pokazuje wykresy cen z najwazniejszymi wskaznikami.
- Pomaga sledzic i analizowac wlasny portfel akcji.
- Dziala dla rynkow polskich, amerykanskich i europejskich.

## Funkcje programu

### 1. Szukaj
- Wyszukiwarka spolek po nazwie lub tickerze (np. `PKO.WA`, `AAPL`).
- Mozna wpisac dowolny ticker z Yahoo Finance i obejrzec jego wykres.

### 2. Obserwowane
- Lista spolek i ETF-ow, ktore chcesz sledzic.
- Kliknij na pozycje, aby zobaczyc jej wykres i analize.

### 3. Okazje (skaner)
- Przycisk **Skanuj okazje** przeszukuje wybrana liste spolek i pokazuje najlepsze kandydatury.
- Do wyboru sa trzy tryby:
  - **Klasyczny** — szuka cialo wycofania w trendzie wzrostowym (np. kurs nizej niz srednia 50 dni, ale powyzej sredniej 200 dni).
  - **Value Investing** — szuka spolek niedowartosciowanych: cena nizej niz uproszczona wartosc wewnetrzna biznesu.
  - **Mieszany** — laczy wskazniki techniczne, Value Investing i wynik backtestu setupu.
- Kazdy wynik ma ocene w skali 0–100, im wyzsza, tym lepsza okazja.
- Kliknij wynik dwa razy, aby otworzyc pelna analize (wykres, wskazniki, makroekonomia, wiadomosci).

### 4. Portfel
- Dodawaj swoje pozycje i sledz ich wartosc.
- **Historia pozycji** — jak zmienial sie portfel w czasie.
- **Weryfikacja sygnalow** — czy Twoje wczesniejsze decyzje byly trafne.
- **Scenariusze** — symulacja, co sie stanie w roznych warunkach rynkowych.
- **Sugestie** — propozycje, co kupic, dodac lub sprzedac.
- **Porada AI** — dodatkowa opinia zewnetrznego modelu AI (wymaga wlasnego klucza API).

### 5. Wykresy
- Wykresy swiecowe z okresami od 1 miesiaca do 5 lat.
- Wskazniki: srednia kroczaca 20, 50 i 200 dni oraz RSI.
- Zakladka **Analiza Fundamentalna** pokazuje podstawowe dane spolek (np. wskazniki P/E, wzrost przychodow).

## Wymagania

- Python 3.10 lub nowszy.
- Po l czenie z internetem (dane pochodza z Yahoo Finance).

## Instalacja

1. Utworz srodowisko wirtualne:

   ```
   python -m venv .venv
   ```

2. Wlacz je:

   - macOS / Linux:
     ```
     source .venv/bin/activate
     ```
   - Windows:
     ```
     .venv\Scripts\activate
     ```

3. Zainstaluj wymagane biblioteki:

   ```
   pip install -r requirements.txt
   ```

## Uruchomienie

```
python main.py
```

## Budowanie wersji wykonywalnej

Program mozna spakowac do pojedynczego pliku aplikacji (np. `.app` na macOS) za pomoca PyInstaller.

## Uwagi

- Program nie jest porada inwestycyjna. Wyniki to tylko wskazowki — decyzje o zakupie podejmujesz samodzielnie.
- Dane moga byc opoznione lub chwilowo niedostepne.
- Pierwsze skanowanie moze trwac kilka minut (program pobiera dane z internetu). Kolejne uruchomienia sa szybsze, bo dane sa przechowywane w pamieci podrecznej.
