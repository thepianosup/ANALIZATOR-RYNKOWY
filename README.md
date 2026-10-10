# Analizator Rynkowy

Aplikacja okienkowa na komputer (macOS) do wyszukiwania okazji rynkowych na giełdzie.
Program sam skanuje listę akcji, indeksów i ETF-ów, analizuje je i pokazuje, które z nich
wyglądają najciekawiej — na podstawie wskaźników technicznych, wyceny (Value Investing)
i sytuacji makroekonomicznej.

Repozytorium GitHub to kopia zapasowa całego kodu. Po sklonowaniu projektu i wykonaniu
kroków z sekcji **Instalacja** program działa od razu — nie trzeba niczego dodatkowo konfigurować.

---

## 1. Co robi program

- Skanuje listę spółek (Polska, USA, Europa), indeksów i ETF-ów.
- Wyszukuje okazje rynkowe: cofnięcia kursu w trendzie wzrostowym oraz spółki niedowartościowane.
- Pokazuje wykresy świecowe ze wskaźnikami (SMA 20/50/200, RSI).
- Ocenia sytuację makroekonomiczną (USA, Europa, Polska) i podaje wiadomości o spółce.
- Pomaga prowadzić własny portfel: zysk/strata, historia, weryfikacja sygnałów, scenariusze, sugestie.
- Opcjonalnie pobiera dodatkową opinię z modelu AI (własny klucz API).

## 2. Funkcje — instrukcja obsługi

### Szukaj
- Wpisz nazwę lub ticker, np. `PKO.WA`, `AAPL`, `SPY`.
- Kliknij dwukrotnie wynik, aby zobaczyć wykres i analizę.
- Gwiazdką możesz dodać spółkę do listy **Obserwowane**.

### Obserwowane
- Twoja lista spółek do śledzenia (zapisywana na dysku).
- Kliknięcie pozycji otwiera jej wykres.

### Okazje (skaner)
- Przycisk **Skanuj okazje** przeszukuje całą listę instrumentów i pokazuje ranking.
- Trzy tryby skanowania:
  - **Klasyczny** — szuka cofnięcia kursu w trendzie wzrostowym (np. kurs poniżej SMA50, ale powyżej SMA200).
  - **Value Investing** — szuka spółek, których cena jest niższa od uproszczonej wartości wewnętrznej.
  - **Mieszany** — łączy wskaźniki techniczne, Value Investing i wynik backtestu setupu.
- Każdy wynik ma ocenę 0–100 (im wyżej, tym lepsza okazja).
- Kliknij dwukrotnie wynik, aby otworzyć pełny raport: podsumowanie i wycena,
  wskaźniki techniczne, analiza fundamentalna, makroekonomia i wiadomości.
- Pierwsze skanowanie trwa kilka minut (pobieranie danych z internetu). Kolejne są znacznie szybsze.

### Portfel
- Dodawaj pozycje (ticker, liczba sztuk, cena zakupu) i śledź aktualny zysk/stratę.
- **Historia pozycji** — jak zmieniała się wartość portfela w czasie.
- **Weryfikacja sygnałów** — czy wcześniejsze sygnały kupna/sprzedaży się sprawdziły.
- **Scenariusze** — symulacja portfela w różnych warunkach rynkowych.
- **Sugestie** — propozycje zmian w portfelu (co kupić, dodać, sprzedać).
- **Porada AI** — opcjonalna opinia zewnętrznego modelu AI (wymaga własnego klucza OpenAI/Gemini,
  wpisywanego w programie).

### Wykresy i analiza fundamentalna
- Świece dla okresów: 1M, 3M, 6M, 1R, 2L, 5L.
- Wskaźniki: SMA20, SMA50, SMA200 oraz RSI.
- Zakładka **Analiza Fundamentalna** pokazuje podstawowe dane spółki (np. P/E, wzrost przychodów).

## 3. Skąd pochodzą dane

- Dane kursowe pochodzą z serwisu **Yahoo Finance** (biblioteka `yfinance`) — nie trzeba zakładać konta.
- Dane mogą być opóźnione o kilkanaście minut lub chwilowo niedostępne.
- Program zapisuje pobrane dane w pamięci podręcznej, żeby nie pobierać ich w kółko.

## 4. Wymagania

- Komputer z macOS (zbudowanie aplikacji `.app` jest przygotowane pod macOS).
- **Python 3.10 lub nowszy** (projekt testowany na Pythonie 3.13).
- Połączenie z internetem.

## 5. Instalacja (krok po kroku)

1. Sklonuj repozytorium (albo pobierz i rozpakuj projekt):

   ```bash
   git clone https://github.com/thepianosup/ANALIZATOR-RYNKOWY.git
   cd ANALIZATOR-RYNKOWY
   ```

2. Utwórz środowisko wirtualne:

   ```bash
   python3 -m venv .venv
   ```

3. Włącz środowisko wirtualne:

   ```bash
   source .venv/bin/activate
   ```

4. Zainstaluj potrzebne biblioteki:

   ```bash
   pip install -r requirements.txt
   ```

## 6. Uruchomienie

```bash
python main.py
```

## 7. Zbudowanie aplikacji .app (opcjonalnie, macOS)

Jeśli chcesz mieć zwykłą ikonę aplikacji zamiast uruchamiania przez terminal:

```bash
pip install pyinstaller
pyinstaller "Analizator Rynkowy.spec"
```

Gotowa aplikacja pojawi się w folderze `dist/` jako `Analizator Rynkowy.app`.
Plik `Analizator Rynkowy.spec` jest w repozytorium, więc build odtwarza się 1:1.
Uwaga: spec jest przygotowany dla macOS — na Windows/Linux wymagałby zmian.

## 8. Gdzie program zapisuje dane

Program **nie zapisuje danych użytkownika w folderze projektu**, tylko w folderze domowym:

```
~/.analizator_rynkowy/
├── cache.db                  — pamięć podręczna pobranych danych (można bezpiecznie usunąć)
├── watchlist.json            — lista „Obserwowane”
├── portfolio.json            — aktualne pozycje portfela
├── portfolio_closed.json     — zamknięte pozycje
└── ai_advice_settings.json   — ustawienia i klucz API funkcji „Porada AI”
```

- **Usunięcie folderu projektu nie usuwa tych danych** — zostają na dysku.
- **Klucz API nie jest w repozytorium** (i nie powinien tam trafić). Po odtworzeniu projektu
  na innym komputerze wystarczy wpisać go ponownie w programie.
- Jeśli chcesz przenieść portfel i ustawienia na inny komputer, skopiuj ręcznie folder
  `~/.analizator_rynkowy`.

## 9. Struktura projektu

```
ANALIZATOR RYNKOWY/
├── main.py                    — punkt startowy programu
├── config.py                  — ustawienia: listy instrumentów, progi skanera, czasy cache
├── requirements.txt           — lista potrzebnych bibliotek
├── ui/                        — okna i zakładki programu (interfejs)
├── analysis/                  — obliczenia: wskaźniki, skaner okazji, Value Investing,
│                                makro, analiza portfela, porada AI
├── data/                      — pobieranie danych z internetu, pamięć podręczna, zapis portfela
├── assets/                    — style wyglądu (style.qss) i biblioteka wykresów (plotly.min.js)
├── icon.png / icon.icns       — ikony aplikacji (potrzebne do zbudowania .app)
├── Analizator Rynkowy.spec    — przepis na zbudowanie aplikacji .app
├── generate_icon.py           — skrypt do wygenerowania ikony icon.png (na wypadek, gdyby jej brakło)
└── README.md                  — ten plik
```

## 10. Konfiguracja

Wszystkie ustawienia są w pliku `config.py`, m.in.:

- listy instrumentów do skanowania (polskie, amerykańskie, europejskie, ETF-y),
- progi skanera okazji (RSI, odchylenie od SMA, wolumen, obsunięcie od maksimum),
- czasy życia pamięci podręcznej.

Możesz dopisywać własne tickery z Yahoo Finance do list w `config.py` — po zapisaniu
zmiany działają od razu przy następnym uruchomieniu.

## 11. Odtworzenie projektu z kopii (GitHub)

Repozytorium zawiera **cały kod potrzebny do uruchomienia programu**, w tym:

- ikony `icon.png` / `icon.icns`,
- plik `Analizator Rynkowy.spec` do zbudowania `.app`,
- bibliotekę wykresów `assets/plotly.min.js`.

Nie ma w nim tylko rzeczy, które tworzy się automatycznie przy instalacji:

- `.venv/` — środowisko wirtualne (tworzy się w kroku 2 instalacji),
- `build/` i `dist/` — foldery zbudowanej aplikacji (powstają przy budowaniu).

Czyli po sklonowaniu repozytorium wystarczy powtórzyć kroki z sekcji **Instalacja** i **Uruchomienie**.

## 12. Możliwe problemy

- **Pierwsze skanowanie trwa długo** — tak ma być, program pobiera dane setek instrumentów.
  Kolejne skanowania są szybkie (dane z pamięci podręcznej).
- **Brak danych / puste wykresy** — sprawdź połączenie z internetem i czy ticker jest poprawny.
- **Aplikacja nie startuje po zmianach** — usuń folder `.venv` i wykonaj instalację od nowa.
- **Dziwne zachowanie po aktualizacji** — usuń `~/.analizator_rynkowy/cache.db`,
  program odtworzy pamięć podręczną.

## 13. Ważne

- Program **nie jest poradą inwestycyjną**. Wyniki to tylko wskazówki — decyzje podejmujesz samodzielnie.
- Dane z Yahoo Finance mogą być opóźnione lub niepełne.
- „Porada AI” to tylko dodatkowa opinia modelu językowego — również nie jest rekomendacją.
- Repozytorium jest publiczne — **nie dodawaj do niego plików z kluczami API ani danymi portfela**.
