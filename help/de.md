# Hilfe — Project Manager

## Überblick
Project Manager verwaltet eine große Anzahl von Projekten in einem Ordner.
Er scannt den Projekt-Stammordner, zeigt alles in einer Tabelle und startet
die Claude Code CLI oder Codex CLI in jedem Projekt mit einem Klick.

Hauptfunktionen:
• Tabelle aller Projekte mit Sortierung und Suche
• KI-Projektanalyse über DeepSeek (was es ist und in welchem Zustand)
• Start von Claude / Codex im ausgewählten Projekt
• Mehrere Projekte als Windows-Terminal-Tabs öffnen (Presets)
• Wichtige Projekte oben anheften
• Aufgaben-Tracker mit Erinnerungen
• Mehrsprachige Oberfläche (5 Sprachen), sofortiger Wechsel

Tipp: Drücke jederzeit F1, um diese Hilfe zu öffnen.

## Projektliste
Der Tab „Projekte“ zeigt jeden Ordner im Projekt-Stamm.

• Suche — das Feld oben filtert nach Name, Beschreibung und Stack.
• Sortierung — Klick auf eine Spaltenüberschrift.
• Doppelklick auf ein Projekt — startet Claude Code.
• Rechtsklick — ein Kontextmenü mit allen Aktionen.

„🔄 Scan (erzwungen)“ scannt den Ordner unter Umgehung des Caches neu.
„📂 Ordner“ öffnet den Projekt-Stamm im Explorer.
„📁 Daten“ öffnet den Ordner mit Einstellungen und Datenbank (%APPDATA%).

## Anheften und Reihenfolge
Angeheftete Projekte erscheinen immer oben in der Liste, gelb hervorgehoben.

• Anheften / lösen — Schaltfläche „📌 Anheften“ oder das Kontextmenü.
• Umsortieren — ziehe ein angeheftetes Projekt mit der Maus nach oben oder
  unten oder verwende Alt+↑ / Alt+↓.

Die Reihenfolge wird automatisch gespeichert und übersteht einen Neustart.

## Claude und Codex starten
Wähle ein Projekt und drücke „▶ Claude“ oder „▶ Codex“ — ein neues
Terminalfenster öffnet sich mit dem Agenten, der bereits im Projektordner läuft.

Der Start nutzt Desktop-Skripte (Claude-BypassProxy, Codex-BypassProxy), die
die Umgebung einrichten und den Proxy umgehen.

„✨ Neu“ erstellt einen neuen Projektordner und startet darin einen Agenten.

## Terminal-Tabs und Presets
„🖥 In Tabs öffnen“ öffnet einen Dialog zum Starten mehrerer Projekte auf
einmal — jedes in einem eigenen Windows-Terminal-Tab mit eigener Farbe und
eigenem Titel.

Ablauf:
1. Markiere die benötigten Projekte (Kontextmenü → „Für Terminal markieren“).
2. Öffne den Dialog, ordne und benenne Tabs bei Bedarf um.
3. Drücke „🚀 Öffnen“.

Ein Preset ist ein gespeicherter Projektsatz. Speichere ihn mit
„💾 Speichern unter…“ und öffne das nächste Mal den ganzen Satz mit einem
Klick — praktisch für die morgendliche Routine „öffne alles, woran ich arbeite“.

Ein Tab-Titel kann per Doppelklick in der Liste oder über das Kontextmenü
der Haupttabelle gesetzt werden. Der Titel wird auch in der Spalte
„Tab-Titel“ der Haupttabelle angezeigt.

## Titel wiederherstellen
Nach einem /resume-Befehl überschreibt Claude den Terminal-Tab-Titel.
Die Schaltfläche „🏷 Titel wiederherstellen“ stellt die Titel zurück.

Das Programm findet die offenen Terminal-Tabs, erkennt, welches Projekt sich
in jedem befindet, und benennt die Tabs wieder in ihre konfigurierten Titel
um. Eine Preset-Bindung ist nicht nötig — die Erkennung ist dynamisch.

## Aufgaben-Tracker
Der Tab „Aufgaben-Tracker“ ist eine Liste von Aufgaben, Ideen und Notizen
pro Projekt.

• Eine Aufgabe kann an ein Projekt gebunden oder „ohne Projekt“ sein.
• Eine Aufgabe hat Typ, Status, Priorität, Tags, ein Fälligkeitsdatum und
  eine Erinnerung.
• Eine Erinnerung wird im Programm ausgelöst; du kannst auch eine
  Windows-Systemerinnerung (über die Aufgabenplanung) erstellen, die auch
  bei geschlossenem Programm ausgelöst wird.
• Die Schaltflächen „🚀 Test in Claude / Codex“ erstellen einen Ideenordner,
  schreiben IDEA.md und starten einen Agenten zur Ausarbeitung der Idee.

## DeepSeek-Analyse
Die Analyse beschreibt, was das Projekt ist, wie es funktioniert und in
welcher Phase es sich befindet.

• „🤖 DS-Analyse“ — analysiert das ausgewählte Projekt.
• „🤖 DS: neue“ — analysiert nur Projekte ohne Beschreibung.
• „🤖 DS: alle“ — analysiert alle Projekte erneut.
• „⏹ Stopp“ — bricht die Massenanalyse ab.

Das Ergebnis wird zwischengespeichert und im rechten Bereich und in der
Tabelle angezeigt.

## Einstellungen und Sprache
• Schriftgröße — die Schaltflächen A− / A+ oder Strg + Mausrad.
• Sprache — das Dropdown oben. Die Oberfläche wechselt sofort, ohne Neustart.
  Russisch, Englisch, Deutsch, Spanisch und Chinesisch sind verfügbar.

Alle Einstellungen und Daten werden in %APPDATA%\ProjectManager gespeichert.
Eine tägliche Sicherung liegt in Documents\ProjectManager-Backups.
