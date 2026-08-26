@echo off਍匀䔀吀䰀伀䌀䄀䰀 䔀渀愀戀氀攀䐀攀氀愀礀攀搀䔀砀瀀愀渀猀椀漀渀ഀ
਍㨀㨀 ⴀⴀⴀ 䌀伀一䘀䤀䜀唀刀䄀吀䤀伀一 ⠀瀀愀琀栀猀 爀攀氀愀琀椀瘀攀 琀漀 琀栀椀猀 猀挀爀椀瀀琀 昀漀氀搀攀爀 漀渀 唀匀䈀⤀ ⴀⴀⴀഀ
SET "SRC_DIR=%~dp0"਍匀䔀吀 ∀䰀伀䌀䄀䰀开䐀匀吀㴀挀㨀尀䜀漀氀搀挀氀甀戀尀猀氀漀琀尀栀眀搀爀椀瘀攀爀猀∀ഀ
SET "REMOTE_DST=\\10.0.0.90\c$\Goldclub\slot\hwdrivers"਍匀䔀吀 ∀䘀䤀䰀䔀匀㴀䰀椀最栀琀猀⸀砀洀氀 䬀攀礀戀漀愀爀搀⸀砀洀氀∀ഀ
਍攀挀栀漀 嬀㄀⼀㌀崀 䌀栀攀挀欀椀渀最 匀漀甀爀挀攀 䘀椀氀攀猀⸀⸀⸀ഀ
if not exist "%SRC_DIR%Lights.xml" (echo Missing Lights.xml in source & pause & exit)਍椀昀 渀漀琀 攀砀椀猀琀 ∀─匀刀䌀开䐀䤀刀─䬀攀礀戀漀愀爀搀⸀砀洀氀∀ ⠀攀挀栀漀 䴀椀猀猀椀渀最 䬀攀礀戀漀愀爀搀⸀砀洀氀 椀渀 猀漀甀爀挀攀 ☀ 瀀愀甀猀攀 ☀ 攀砀椀琀⤀ഀ
਍㨀㨀 ⴀⴀⴀ 䰀伀䌀䄀䰀 䄀吀吀䔀䴀倀吀 ⴀⴀⴀഀ
echo [2/3] Checking Local Destination: %LOCAL_DST%਍椀昀 攀砀椀猀琀 ∀─䰀伀䌀䄀䰀开䐀匀吀─尀∀ ⠀ഀ
    echo Local directory found. Replacing files...਍    昀漀爀 ──䘀 椀渀 ⠀─䘀䤀䰀䔀匀─⤀ 搀漀 ⠀ഀ
        xcopy "%SRC_DIR%%%F" "%LOCAL_DST%\" /Y /R਍    ⤀ഀ
    echo Local Update Complete.਍    最漀琀漀 䘀䤀一䤀匀䠀ഀ
)਍ഀ
:: --- REMOTE RETRY ATTEMPT ---਍攀挀栀漀 䰀漀挀愀氀 瀀愀琀栀 渀漀琀 昀漀甀渀搀⸀ 䌀栀攀挀欀椀渀最 刀攀洀漀琀攀 䐀攀猀琀椀渀愀琀椀漀渀㨀 ─刀䔀䴀伀吀䔀开䐀匀吀─ഀ
if exist "%REMOTE_DST%\" (਍    攀挀栀漀 刀攀洀漀琀攀 瀀愀琀栀 愀挀琀椀瘀攀⸀ 匀琀愀爀琀椀渀最 刀漀戀漀挀漀瀀礀 眀椀琀栀 刀攀琀爀椀攀猀⸀⸀⸀ഀ
    robocopy "%SRC_DIR%" "%REMOTE_DST%" %FILES% /R:5 /W:5 /NP /IS /IT਍    ഀ
    if !ERRORLEVEL! LEQ 3 (਍        攀挀栀漀 刀攀洀漀琀攀 唀瀀搀愀琀攀 匀甀挀挀攀猀猀昀甀氀⸀ഀ
    ) else (਍        攀挀栀漀 刀漀戀漀挀漀瀀礀 昀愀椀氀攀搀 眀椀琀栀 攀爀爀漀爀 挀漀搀攀 ℀䔀刀刀伀刀䰀䔀嘀䔀䰀℀⸀ഀ
    )਍⤀ 攀氀猀攀 ⠀ഀ
    echo ERROR: Could not find Local or Remote destination. Check network/permissions.਍⤀ഀ
਍㨀䘀䤀一䤀匀䠀ഀ
echo ------------------------------------------਍攀挀栀漀 倀爀漀挀攀猀猀 䘀椀渀椀猀栀攀搀⸀ഀ
pause਍ഀ�