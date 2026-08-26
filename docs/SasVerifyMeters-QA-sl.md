# SasVerifyMeters — sporočilo za QA (sl)

Kopiraj spodnje v e-pošto / Teams / Confluence.

---

Živjo,

kratko o **`SasVerifyMeters.exe`** — zakaj ga uporabljamo v lab/QA:

To ni IGT SAS tester. Namen je **v živo spremljati accounting** na kabinetu: **živi SAS metri** (COM/MUX) proti **Machine** posnetku (`DeviceManagerData`), v istih zavihkih kot na EGM (Master, Bills, Transfer …).

**Tipičen use case:** med igranjem igre imaš SasVerifyMeters na drugem monitorju (ali poleg igre) in **v živo vidiš spremembe metrov** — **brez da greš v meters meni na EGMju**. Po RAM clearju ali novem buildu pa hitro preveriš, ali se SAS in Machine ujemata.

**Kje dobiš program**

- **Lab share (priporočeno):** `\\10.0.0.249\WinSystems_SLOT\_Tools\SasVerifyMeters`  
  Zaženi **`SasVerifyMeters.exe`** iz te mape (ali kopiraj mapo na USB in od tam na kabinet).
- **Z delovne postaje** (lab PC): remote scan root npr. `\\10.0.0.90\c$\Goldclub\var` + COM/MUX na ta PC.
- **Lokalno na kabinetu** (npr. z USB-ja): zaženeš exe neposredno na EGMju — **Local / ta računalnik**. Machine bere lokalni `C:\Goldclub\var`. Živega SAS/MUX compare na samem EGMju ni — orodje primerja lokalne state mape (gm2au vs SASControler1). Za živi SAS primerjavo ostane lab PC + kabel.

**Opozorilo — samo ena instanca**

Naenkrat odpri **samo en** SasVerifyMeters. Dva okna tekmujeta za COM in osveževanje metrov.

**Opozorilo — IGT SAS tester hkrati**

Če ima kdo na **isti napravi** (isti COM) istočasno vklopljen **IGT SAS tester / SASHost**, COM zasede samo ena aplikacija. SasVerifyMeters potem ponudi prompt, da metri (Machine + SASControler) **pridejo prek share-a** (UNC), ne prek živega SAS/MUX. Če prompt **potrdite**, se vsi podatki izvzamejo prek share-a — to ni več živi COM capture. Za pravi živi compare zaprite IGT tester, nato Refresh / Auto fetch.

**Osnovni potek (lab PC, živi SAS):**

1. Zapri IGT SASHost, poveži SAS kabel/MUX, izberi COM.
2. Scan root: npr. `\\10.0.0.90\c$\Goldclub\var` (igra mora teči na kabinetu).
3. **Auto fetch** vklopljen = samodejno osveževanje; izklopljen = samo **Refresh Meters**.
4. Master: **Handpay In** = ročni vnos (pago manual) z DeviceManagerja — **ni** SAS koda 0023 (to je handpay **out**).

Help v app: **Help / F1**. Ob mismatchu pošljite screenshot Master + Accounting.

Lep pozdrav