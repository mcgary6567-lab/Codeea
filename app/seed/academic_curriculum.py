"""Academic curriculum seed: books, chapters and lessons with real Arabic/Quranic content, plus per-student
progress, lesson plans, evaluations, monthly tests, dor schedules and certificates.

Called at the end of app/seed/academic.py. Idempotent: everything checks before inserting.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.academic import (Book, Certificate, Chapter, Course, CurriculumVersion, DorSchedule, Evaluation, Lesson,
                                 LessonAnnotation, LessonPlan, MonthlyTest, StudentProgress)
from app.models.core import User
from app.models.people import Student, Teacher

# =============================================================================== Quranic source text
# (surah number -> (english name, arabic name, [(ayah, arabic with tashkeel, english translation)]))
QURAN: dict[int, tuple[str, str, list[tuple[int, str, str]]]] = {
    1: ("Al-Fatiha", "الفاتحة", [
        (1, "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ", "In the name of Allah, the Most Gracious, the Most Merciful."),
        (2, "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ", "All praise belongs to Allah, Lord of all the worlds."),
        (3, "الرَّحْمَٰنِ الرَّحِيمِ", "The Most Gracious, the Most Merciful."),
        (4, "مَالِكِ يَوْمِ الدِّينِ", "Master of the Day of Judgement."),
        (5, "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ", "You alone we worship, and You alone we ask for help."),
        (6, "اهْدِنَا الصِّرَاطَ الْمُسْتَقِيمَ", "Guide us along the straight path."),
        (7, "صِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ",
         "The path of those You have blessed, not of those who earned anger, nor of those who went astray."),
    ]),
    103: ("Al-Asr", "العصر", [
        (1, "وَالْعَصْرِ", "By the passage of time."),
        (2, "إِنَّ الْإِنسَانَ لَفِي خُسْرٍ", "Indeed, mankind is in loss."),
        (3, "إِلَّا الَّذِينَ آمَنُوا وَعَمِلُوا الصَّالِحَاتِ وَتَوَاصَوْا بِالْحَقِّ وَتَوَاصَوْا بِالصَّبْرِ",
         "Except those who believe, do righteous deeds, and urge one another to truth and to patience."),
    ]),
    105: ("Al-Fil", "الفيل", [
        (1, "أَلَمْ تَرَ كَيْفَ فَعَلَ رَبُّكَ بِأَصْحَابِ الْفِيلِ", "Have you not seen how your Lord dealt with the companions of the elephant?"),
        (2, "أَلَمْ يَجْعَلْ كَيْدَهُمْ فِي تَضْلِيلٍ", "Did He not make their plot go astray?"),
        (3, "وَأَرْسَلَ عَلَيْهِمْ طَيْرًا أَبَابِيلَ", "And He sent against them birds in flocks."),
        (4, "تَرْمِيهِم بِحِجَارَةٍ مِّن سِجِّيلٍ", "Striking them with stones of hard clay."),
        (5, "فَجَعَلَهُمْ كَعَصْفٍ مَّأْكُولٍ", "And He made them like eaten straw."),
    ]),
    106: ("Quraysh", "قريش", [
        (1, "لِإِيلَافِ قُرَيْشٍ", "For the familiarity of the Quraysh."),
        (2, "إِيلَافِهِمْ رِحْلَةَ الشِّتَاءِ وَالصَّيْفِ", "Their familiarity with the journeys of winter and summer."),
        (3, "فَلْيَعْبُدُوا رَبَّ هَٰذَا الْبَيْتِ", "So let them worship the Lord of this House."),
        (4, "الَّذِي أَطْعَمَهُم مِّن جُوعٍ وَآمَنَهُم مِّنْ خَوْفٍ", "Who fed them against hunger and made them safe from fear."),
    ]),
    107: ("Al-Ma'un", "الماعون", [
        (1, "أَرَأَيْتَ الَّذِي يُكَذِّبُ بِالدِّينِ", "Have you seen the one who denies the Recompense?"),
        (2, "فَذَٰلِكَ الَّذِي يَدُعُّ الْيَتِيمَ", "That is the one who drives away the orphan."),
        (3, "وَلَا يَحُضُّ عَلَىٰ طَعَامِ الْمِسْكِينِ", "And does not encourage the feeding of the poor."),
        (4, "فَوَيْلٌ لِّلْمُصَلِّينَ", "So woe to those who pray,"),
        (5, "الَّذِينَ هُمْ عَن صَلَاتِهِمْ سَاهُونَ", "Those who are heedless of their prayer."),
        (6, "الَّذِينَ هُمْ يُرَاءُونَ", "Those who make a show of their deeds,"),
        (7, "وَيَمْنَعُونَ الْمَاعُونَ", "And withhold small acts of kindness."),
    ]),
    108: ("Al-Kawthar", "الكوثر", [
        (1, "إِنَّا أَعْطَيْنَاكَ الْكَوْثَرَ", "Indeed, We have granted you al-Kawthar."),
        (2, "فَصَلِّ لِرَبِّكَ وَانْحَرْ", "So pray to your Lord and sacrifice."),
        (3, "إِنَّ شَانِئَكَ هُوَ الْأَبْتَرُ", "Indeed, the one who hates you is the one cut off."),
    ]),
    109: ("Al-Kafirun", "الكافرون", [
        (1, "قُلْ يَا أَيُّهَا الْكَافِرُونَ", "Say: O disbelievers,"),
        (2, "لَا أَعْبُدُ مَا تَعْبُدُونَ", "I do not worship what you worship."),
        (3, "وَلَا أَنتُمْ عَابِدُونَ مَا أَعْبُدُ", "Nor are you worshippers of what I worship."),
        (4, "وَلَا أَنَا عَابِدٌ مَّا عَبَدتُّمْ", "Nor will I be a worshipper of what you worship."),
        (5, "وَلَا أَنتُمْ عَابِدُونَ مَا أَعْبُدُ", "Nor will you be worshippers of what I worship."),
        (6, "لَكُمْ دِينُكُمْ وَلِيَ دِينِ", "For you is your religion, and for me is mine."),
    ]),
    110: ("An-Nasr", "النصر", [
        (1, "إِذَا جَاءَ نَصْرُ اللَّهِ وَالْفَتْحُ", "When the victory of Allah has come, and the conquest,"),
        (2, "وَرَأَيْتَ النَّاسَ يَدْخُلُونَ فِي دِينِ اللَّهِ أَفْوَاجًا", "And you see the people entering the religion of Allah in crowds,"),
        (3, "فَسَبِّحْ بِحَمْدِ رَبِّكَ وَاسْتَغْفِرْهُ إِنَّهُ كَانَ تَوَّابًا",
         "Then glorify the praise of your Lord and seek His forgiveness. Indeed, He is ever Accepting of repentance."),
    ]),
    111: ("Al-Lahab", "المسد", [
        (1, "تَبَّتْ يَدَا أَبِي لَهَبٍ وَتَبَّ", "May the hands of Abu Lahab perish, and perish he."),
        (2, "مَا أَغْنَىٰ عَنْهُ مَالُهُ وَمَا كَسَبَ", "His wealth and what he earned availed him nothing."),
        (3, "سَيَصْلَىٰ نَارًا ذَاتَ لَهَبٍ", "He will burn in a fire of blazing flame."),
        (4, "وَامْرَأَتُهُ حَمَّالَةَ الْحَطَبِ", "And his wife, the carrier of firewood."),
        (5, "فِي جِيدِهَا حَبْلٌ مِّن مَّسَدٍ", "Around her neck is a rope of twisted fibre."),
    ]),
    112: ("Al-Ikhlas", "الإخلاص", [
        (1, "قُلْ هُوَ اللَّهُ أَحَدٌ", "Say: He is Allah, the One."),
        (2, "اللَّهُ الصَّمَدُ", "Allah, the Eternal Refuge."),
        (3, "لَمْ يَلِدْ وَلَمْ يُولَدْ", "He neither begets nor is born."),
        (4, "وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ", "Nor is there to Him any equivalent."),
    ]),
    113: ("Al-Falaq", "الفلق", [
        (1, "قُلْ أَعُوذُ بِرَبِّ الْفَلَقِ", "Say: I seek refuge in the Lord of the daybreak,"),
        (2, "مِن شَرِّ مَا خَلَقَ", "From the evil of that which He created,"),
        (3, "وَمِن شَرِّ غَاسِقٍ إِذَا وَقَبَ", "And from the evil of darkness when it settles,"),
        (4, "وَمِن شَرِّ النَّفَّاثَاتِ فِي الْعُقَدِ", "And from the evil of the blowers in knots,"),
        (5, "وَمِن شَرِّ حَاسِدٍ إِذَا حَسَدَ", "And from the evil of an envier when he envies."),
    ]),
    114: ("An-Nas", "الناس", [
        (1, "قُلْ أَعُوذُ بِرَبِّ النَّاسِ", "Say: I seek refuge in the Lord of mankind,"),
        (2, "مَلِكِ النَّاسِ", "The Sovereign of mankind,"),
        (3, "إِلَٰهِ النَّاسِ", "The God of mankind,"),
        (4, "مِن شَرِّ الْوَسْوَاسِ الْخَنَّاسِ", "From the evil of the retreating whisperer,"),
        (5, "الَّذِي يُوَسْوِسُ فِي صُدُورِ النَّاسِ", "Who whispers in the breasts of mankind,"),
        (6, "مِنَ الْجِنَّةِ وَالنَّاسِ", "From among the jinn and mankind."),
    ]),
}


def _L(title, arabic="", translation="", objectives="", tajweed="", minutes=30, surah=None, af=None, at=None) -> dict:
    return {"title": title, "arabic_text": arabic or None, "translation": translation or None, "objectives": objectives or None,
            "tajweed_notes": tajweed or None, "expected_minutes": minutes, "surah_number": surah, "ayah_from": af, "ayah_to": at}


def surah_full(num: int, title: str | None = None, minutes: int = 30, objectives: str = "", tajweed: str = "") -> dict:
    name, ar_name, ayat = QURAN[num]
    return _L(title or f"Surah {name} ({num}) — full surah",
              "\n".join(a[1] for a in ayat),
              "\n".join(f"{a[0]}. {a[2]}" for a in ayat),
              objectives or f"Recite Surah {name} ({ar_name}) from beginning to end with correct makhaarij and no reading errors.",
              tajweed or "Observe the ghunnah on every noon/meem mushaddad and complete each madd for its full count.",
              minutes, num, ayat[0][0], ayat[-1][0])


def surah_by_ayah(num: int, minutes: int = 30) -> list[dict]:
    name, ar_name, ayat = QURAN[num]
    out = []
    for n, arabic, tr in ayat:
        out.append(_L(f"Surah {name} — Ayah {n}", arabic, tr,
                      f"Read ayah {n} of Surah {name} ({ar_name}) fluently, three times without correction.",
                      "Stop correctly at the end of the ayah; apply sukoon on the final letter when pausing.",
                      minutes, num, n, n))
    return out


# =============================================================================== curriculum definitions
# course code -> [(book title, arabic title, description, [(chapter title, chapter arabic, [lessons])])]
def _curriculum() -> dict[str, list]:
    return {
        "QAIDA": [
            ("Norani Qaida — Part 1: Letters & Sounds", "القاعدة النورانية ١", "The 29 Arabic letters, their sounds and their written shapes.", [
                ("The Arabic Letters", "حروف الهجاء", [
                    _L("Alif to Kha", "ا  ب  ت  ث  ج  ح  خ", "alif · ba · ta · tha · jeem · ha · kha",
                       "Name and pronounce the first seven letters in isolation.", "Distinguish ح (haa) from خ (khaa) and ه (ha) at the throat.", 25),
                    _L("Dal to Shin", "د  ذ  ر  ز  س  ش", "dal · dhal · ra · zay · seen · sheen",
                       "Name and pronounce letters 8–13 in isolation.", "ر is rolled lightly once; ذ is pronounced with the tongue tip touching the upper teeth.", 25),
                    _L("Sad to Zha", "ص  ض  ط  ظ", "sad · dad · ta (heavy) · zha",
                       "Produce the four heavy (mufakhkham) letters correctly.", "These are letters of isti'la — the tongue rises and the sound is full-mouthed.", 30),
                    _L("Ain to Qaf", "ع  غ  ف  ق", "ain · ghain · fa · qaf",
                       "Produce ع and غ from the middle of the throat.", "ق is a qalqalah letter: it echoes when it carries sukoon.", 30),
                    _L("Kaf to Ya", "ك  ل  م  ن  و  ه  ء  ي", "kaf · lam · meem · noon · waw · ha · hamza · ya",
                       "Complete the alphabet and recite all 29 letters in order.", "م and ن carry ghunnah when they are doubled.", 30),
                ]),
                ("Letter Shapes & Joining", "أشكال الحروف", [
                    _L("Beginning forms", "بـ  تـ  جـ  سـ  عـ  كـ  هـ", "Initial forms of the joining letters.",
                       "Recognise a letter at the start of a word.", "", 25),
                    _L("Middle forms", "ـبـ  ـتـ  ـجـ  ـسـ  ـعـ  ـكـ  ـهـ", "Medial forms of the joining letters.",
                       "Recognise a letter in the middle of a word.", "", 25),
                    _L("End forms", "ـب  ـت  ـج  ـس  ـع  ـك  ـه", "Final forms of the joining letters.",
                       "Recognise a letter at the end of a word.", "", 25),
                    _L("Non-joining letters", "ا  د  ذ  ر  ز  و", "The six letters that never join to the letter after them.",
                       "Identify the six non-connectors when reading.", "", 25),
                    _L("Joining practice", "بـتـث  جـحـخ  سـشـص  كـلـمـن", "Joined letter chains for reading drill.",
                       "Read joined chains smoothly without separating the letters.", "", 30),
                ]),
            ]),
            ("Norani Qaida — Part 2: Harakat & Tanween", "القاعدة النورانية ٢", "Short vowels and nunation applied to every letter.", [
                ("The Three Harakat", "الحركات الثلاث", [
                    _L("Fathah", "بَ  تَ  ثَ  جَ  حَ  خَ  دَ  ذَ  رَ  زَ", "ba · ta · tha · ja · ha · kha · da · dha · ra · za",
                       "Read every letter with fathah in one breath.", "Fathah opens the mouth; keep heavy letters full and light letters thin.", 25),
                    _L("Kasrah", "بِ  تِ  ثِ  جِ  حِ  خِ  دِ  ذِ  رِ  زِ", "bi · ti · thi · ji · hi · khi · di · dhi · ri · zi",
                       "Read every letter with kasrah accurately.", "Kasrah lowers the jaw — ر with kasrah becomes thin (muraqqaq).", 25),
                    _L("Dammah", "بُ  تُ  ثُ  جُ  حُ  خُ  دُ  ذُ  رُ  زُ", "bu · tu · thu · ju · hu · khu · du · dhu · ru · zu",
                       "Read every letter with dammah accurately.", "Dammah rounds the lips without adding a waw sound.", 25),
                    _L("Mixed harakat drill", "بَ بِ بُ  تَ تِ تُ  ثَ ثِ ثُ  جَ جِ جُ", "Mixed vowel drill across the letters.",
                       "Switch between the three harakat without hesitation.", "", 30),
                ]),
                ("Tanween", "التنوين", [
                    _L("Tanween Fath", "بًا  تًا  ثًا  جًا  حًا", "an · tan · than · jan · han",
                       "Read double-fathah endings with the correct 'an' sound.", "Tanween behaves exactly like a noon sakinah for the four noon rules.", 25),
                    _L("Tanween Kasr", "بٍ  تٍ  ثٍ  جٍ  حٍ", "in · tin · thin · jin · hin",
                       "Read double-kasrah endings correctly.", "", 25),
                    _L("Tanween Damm", "بٌ  تٌ  ثٌ  جٌ  حٌ", "un · tun · thun · jun · hun",
                       "Read double-dammah endings correctly.", "", 25),
                    _L("Tanween in words", "أَحَدٌ  خُسْرٍ  طَيْرًا  حَبْلٌ", "One · loss · birds · a rope",
                       "Read Quranic words that end in tanween.", "Watch for ikhfa when tanween meets one of the fifteen ikhfa letters.", 30),
                ]),
            ]),
            ("Norani Qaida — Part 3: Sukoon, Shaddah & Madd", "القاعدة النورانية ٣", "Silent letters, doubling and prolongation — the bridge to Nazra.", [
                ("Sukoon", "السكون", [
                    _L("Sukoon on light letters", "أَبْ  أَتْ  أَثْ  أَسْ  أَشْ", "ab · at · ath · as · ash",
                       "Stop the sound cleanly on a letter carrying sukoon.", "No vowel may be added after a sakin letter.", 25),
                    _L("Qalqalah letters with sukoon", "أَقْ  أَطْ  أَبْ  أَجْ  أَدْ", "The five qalqalah letters with sukoon.",
                       "Produce a light echo on ق ط ب ج د when they carry sukoon.", "Qalqalah sughra inside a word; qalqalah kubra when stopping at the end.", 30),
                    _L("Sukoon in words", "قُلْ  يَلِدْ  أَنْعَمْتَ  فَاعْبُدْ", "Say · begets · You have blessed · so worship",
                       "Read real Quranic words containing sakin letters.", "", 30),
                ]),
                ("Shaddah", "الشدة", [
                    _L("Shaddah basics", "رَبَّ  حَجَّ  مَدَّ  شَدَّ", "Lord · pilgrimage · he extended · he tightened",
                       "Hold a doubled letter for the length of two letters.", "A shaddah letter is a sakin letter followed by a voweled one.", 25),
                    _L("Ghunnah letters with shaddah", "إِنَّ  ثُمَّ  النَّاسِ  الْجَنَّةِ", "Indeed · then · mankind · the garden",
                       "Hold the nasal sound for two counts on نّ and مّ.", "Ghunnah mushaddadah is obligatory and lasts two harakat.", 30),
                    _L("Shaddah with tanween", "أَحَدٌ  حَمَّالَةَ  النَّفَّاثَاتِ", "One · carrier · the blowers",
                       "Combine doubling and nunation in one word.", "", 30),
                ]),
                ("Madd Letters", "حروف المد", [
                    _L("Madd Alif", "بَا  تَا  ثَا  قَا  مَا", "Prolongation with alif after fathah.",
                       "Hold the alif for two counts, no more and no less.", "Madd asli (natural madd) is exactly two harakat.", 25),
                    _L("Madd Ya", "بِي  تِي  ثِي  قِي  فِي", "Prolongation with ya after kasrah.",
                       "Hold the ya for two counts.", "", 25),
                    _L("Madd Waw", "بُو  تُو  ثُو  قُو  يَقُو", "Prolongation with waw after dammah.",
                       "Hold the waw for two counts.", "", 25),
                    _L("Reading readiness test", "قُلْ هُوَ اللَّهُ أَحَدٌ", "Say: He is Allah, the One.",
                       "Read a complete Quranic ayah applying every Qaida rule learned.", "Combine ghunnah, madd and qalqalah in a single ayah.", 30, 112, 1, 1),
                ]),
            ]),
        ],
        "NAZRA": [
            ("Surah Al-Fatiha & the Openings", "الفاتحة والافتتاح", "The Opening of the Book, read ayah by ayah.", [
                ("Surah Al-Fatiha", "سورة الفاتحة", surah_by_ayah(1)),
                ("Fluency Drill", "تمرين الطلاقة", [
                    surah_full(1, "Al-Fatiha — complete recitation", 30,
                               "Recite the whole surah in one breath group per ayah with no correction.",
                               "Stop at every ayah ending; observe the madd in الرَّحْمَٰنِ and الضَّالِّينَ (madd lazim, six counts)."),
                    _L("Al-Fatiha — pace and stops", "غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ",
                       "Not of those who earned anger, nor of those who went astray.",
                       "Control breathing across the longest ayah of the surah.",
                       "الضَّالِّينَ carries madd lazim kalimi muthaqqal — hold for six counts.", 25, 1, 7, 7),
                ]),
            ]),
            ("Juz Amma — Reading, Part 1", "جزء عم ١", "The three Quls and the shortest surahs of Juz Amma.", [
                ("The Three Quls", "المعوذات", [
                    surah_full(112), surah_full(113), surah_full(114),
                ]),
                ("Short Surahs", "قصار السور", [
                    surah_full(108), surah_full(103), surah_full(110),
                ]),
            ]),
            ("Juz Amma — Reading, Part 2", "جزء عم ٢", "Narrative and declaration surahs of Juz Amma.", [
                ("Surahs of the Elephant", "سور الفيل", [
                    surah_full(105), surah_full(106), surah_full(107),
                ]),
                ("Declaration Surahs", "سور البراءة", [
                    surah_full(109), surah_full(111),
                ]),
            ]),
        ],
        "HIFZ": [
            ("Al-Fatiha & the Three Quls", "الفاتحة والمعوذات", "First memorisation block: the surahs of daily prayer.", [
                ("Surah Al-Fatiha", "سورة الفاتحة", surah_by_ayah(1, 35)),
                ("The Three Quls", "المعوذات", [
                    surah_full(112, "Hifz — Surah Al-Ikhlas", 35, "Memorise Surah Al-Ikhlas and recite it from memory without prompting."),
                    surah_full(113, "Hifz — Surah Al-Falaq", 35, "Memorise Surah Al-Falaq and recite it from memory without prompting."),
                    surah_full(114, "Hifz — Surah An-Nas", 35, "Memorise Surah An-Nas and recite it from memory without prompting."),
                ]),
            ]),
            ("Juz Amma Memorisation — Part 1", "حفظ جزء عم ١", "Short surahs committed to memory with daily sabaq and sabqi.", [
                ("Sabaq — New Memorisation", "السبق", [
                    surah_full(108, "Hifz — Surah Al-Kawthar", 35),
                    surah_full(103, "Hifz — Surah Al-Asr", 35),
                    surah_full(110, "Hifz — Surah An-Nasr", 35),
                    surah_full(107, "Hifz — Surah Al-Ma'un", 40),
                ]),
                ("Sabqi — Recent Revision", "السبقي", [
                    _L("Sabqi cycle — Al-Ikhlas to An-Nas", "قُلْ هُوَ اللَّهُ أَحَدٌ … مِنَ الْجِنَّةِ وَالنَّاسِ",
                       "Continuous revision of the three Quls.", "Recite the last three memorised surahs in one sitting without error.",
                       "Maintain consistent tempo; do not rush the ghunnah.", 20),
                    _L("Sabqi cycle — Al-Kawthar to Al-Ma'un", "إِنَّا أَعْطَيْنَاكَ الْكَوْثَرَ … وَيَمْنَعُونَ الْمَاعُونَ",
                       "Continuous revision of the current memorisation block.", "Recite four surahs consecutively from memory.", "", 20),
                ]),
            ]),
            ("Juz Amma Memorisation — Part 2", "حفظ جزء عم ٢", "The narrative surahs of the last juz.", [
                ("Sabaq — New Memorisation", "السبق", [
                    surah_full(105, "Hifz — Surah Al-Fil", 40),
                    surah_full(106, "Hifz — Surah Quraysh", 35),
                    surah_full(109, "Hifz — Surah Al-Kafirun", 40),
                    surah_full(111, "Hifz — Surah Al-Lahab", 40),
                ]),
            ]),
            ("Dor — Revision Cycle", "الدور", "Structured long-term revision so nothing memorised is ever lost.", [
                ("Weekly Dor", "الدور الأسبوعي", [
                    _L("Dor block 1 — Al-Fatiha & the Quls", "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ … مِنَ الْجِنَّةِ وَالنَّاسِ",
                       "Revision of the first memorisation block.", "Recite block 1 from memory in under five minutes with no more than one prompt.", "", 20),
                    _L("Dor block 2 — Al-Kawthar to Al-Ma'un", "إِنَّا أَعْطَيْنَاكَ الْكَوْثَرَ … وَيَمْنَعُونَ الْمَاعُونَ",
                       "Revision of the second memorisation block.", "Recite block 2 from memory with no prompt.", "", 20),
                    _L("Dor block 3 — Al-Fil to Al-Lahab", "أَلَمْ تَرَ كَيْفَ فَعَلَ رَبُّكَ بِأَصْحَابِ الْفِيلِ … فِي جِيدِهَا حَبْلٌ مِّن مَّسَدٍ",
                       "Revision of the third memorisation block.", "Recite block 3 from memory with no prompt.", "", 20),
                    _L("Monthly full dor", "من الفاتحة إلى الناس", "Complete revision of everything memorised to date.",
                       "Recite the full memorised portion in a single sitting before the monthly test.", "", 45),
                ]),
            ]),
        ],
        "TAJWEED": [
            ("Makhaarij & Sifaat", "المخارج والصفات", "Where each letter is produced and what characterises it.", [
                ("Points of Articulation", "مخارج الحروف", [
                    _L("The throat letters", "ء  ه  ع  ح  غ  خ", "hamza · ha · ain · haa · ghain · kha",
                       "Produce the six throat letters from their three distinct depths.",
                       "Deepest: ء ه — middle: ع ح — nearest the mouth: غ خ. Never let ح slip into ه.", 30),
                    _L("The tongue letters", "ق  ك  ج  ش  ي  ض  ل  ن  ر  ط  د  ت  ص  ز  س  ظ  ذ  ث",
                       "The eighteen letters produced by the tongue.",
                       "Locate each tongue letter on the correct part of the tongue and palate.",
                       "ض is the hardest letter of the language: the edge of the tongue against the upper molars.", 30),
                    _L("The lip letters", "ف  و  ب  م", "fa · waw · ba · meem",
                       "Produce the four lip letters without exaggeration.", "ب closes the lips fully; و rounds them without touching.", 25),
                    _L("The nasal cavity", "إِنَّ  ثُمَّ  مِن نُّورٍ", "Indeed · then · from a light",
                       "Produce ghunnah from the nose, not the mouth.", "Ghunnah is the natural sound of ن and م; hold it for two harakat.", 25),
                ]),
                ("Characteristics of Letters", "صفات الحروف", [
                    _L("Hams and Jahr", "فَحَثَّهُ شَخْصٌ سَكَتْ", "The ten letters of hams (whispered breath).",
                       "Distinguish whispered letters from voiced ones.", "Hams letters allow the breath to flow; jahr letters stop it.", 30),
                    _L("Shiddah, Tawassut and Rakhawah", "أَجِدْ قَطٍ بَكَتْ  ·  لِنْ عُمَرْ", "Strength, mediumness and softness.",
                       "Classify letters by how completely the sound stops.", "", 30),
                    _L("Isti'la and Istifal", "خُصَّ ضَغْطٍ قِظْ", "The seven heavy (mufakhkham) letters.",
                       "Give the seven heavy letters their full mouth resonance.", "خ ص ض غ ط ق ظ raise the back of the tongue and fill the mouth.", 30),
                    _L("Qalqalah", "قُطْبُ جَدٍ  ·  أَحَدْ  ·  الْفَلَقْ", "The five echoing letters: ق ط ب ج د.",
                       "Produce a clean echo on the five qalqalah letters.",
                       "Qalqalah sughra inside a word, kubra when stopping on the letter at the end of an ayah.", 30),
                ]),
            ]),
            ("Noon Sakinah, Tanween & Meem Sakinah", "أحكام النون والميم", "The four rules of noon sakinah and the three of meem sakinah.", [
                ("Rules of Noon Sakinah & Tanween", "أحكام النون الساكنة والتنوين", [
                    _L("Izhar Halqi — clear pronunciation", "مَنْ آمَنَ  ·  مِنْ خَيْرٍ  ·  أَنْعَمْتَ  ·  عَذَابٌ عَظِيمٌ",
                       "Whoever believes · from good · You have blessed · a great punishment",
                       "Pronounce noon sakinah clearly before the six throat letters.",
                       "Izhar letters: ء ه ع ح غ خ. No ghunnah is added — the noon is crisp.", 30),
                    _L("Idgham — merging", "مَن يَعْمَلْ  ·  مِن رَّبِّهِمْ  ·  مِن نُّورٍ  ·  مِن وَلِيٍّ",
                       "Whoever acts · from their Lord · from a light · any protector",
                       "Merge noon sakinah into the letters of يرملون.",
                       "With ghunnah for ي ن م و; without ghunnah for ل and ر.", 35),
                    _L("Iqlab — conversion to meem", "مِنۢ بَعْدِ  ·  أَنۢبِئْهُمْ  ·  سَمِيعٌۢ بَصِيرٌ",
                       "After · inform them · All-Hearing, All-Seeing",
                       "Convert noon sakinah to a hidden meem before ب.", "Iqlab has one letter only: ب. Hold the ghunnah for two counts.", 30),
                    _L("Ikhfa Haqiqi — hiding", "مِن قَبْلُ  ·  أَنتُمْ  ·  مِن شَرِّ  ·  رِيحًا صَرْصَرًا",
                       "Before · you (pl.) · from the evil of · a furious wind",
                       "Hide the noon with ghunnah before the fifteen ikhfa letters.",
                       "The tongue prepares the next letter while the nasal sound is held for two counts.", 35),
                ]),
                ("Rules of Meem Sakinah", "أحكام الميم الساكنة", [
                    _L("Ikhfa Shafawi", "تَرْمِيهِم بِحِجَارَةٍ  ·  وَهُم بِالْآخِرَةِ",
                       "Striking them with stones · and they, in the Hereafter",
                       "Hide meem sakinah before ب with ghunnah.", "The lips touch lightly; the ghunnah lasts two counts.", 30),
                    _L("Idgham Mithlain Sagheer", "لَهُم مَّا  ·  كَم مِّن فِئَةٍ", "For them is what · how many a group",
                       "Merge meem sakinah into a following meem.", "Merging with obligatory ghunnah of two counts.", 25),
                    _L("Izhar Shafawi", "الْحَمْدُ  ·  أَمْ لَمْ  ·  عَلَيْهِمْ غَيْرِ",
                       "All praise · or did not · upon them, not",
                       "Pronounce meem sakinah clearly before all other letters.", "Take special care before و and ف — no hiding.", 25),
                ]),
                ("Ghunnah", "الغنة", [
                    _L("Noon and Meem Mushaddad", "إِنَّ  ·  ثُمَّ  ·  النَّاسِ  ·  الْجَنَّةِ  ·  حَمَّالَةَ",
                       "Indeed · then · mankind · the garden · carrier",
                       "Hold the obligatory ghunnah for a full two counts on every doubled noon and meem.",
                       "This is the strongest level of ghunnah in the language.", 25),
                ]),
            ]),
            ("Madd & Waqf", "المد والوقف", "Prolongation lengths and where to stop.", [
                ("Types of Madd", "أنواع المد", [
                    _L("Madd Asli — natural prolongation", "قَالَ  ·  يَقُولُ  ·  قِيلَ  ·  نَسْتَعِينُ",
                       "He said · he says · it was said · we seek help",
                       "Hold every natural madd for exactly two counts.", "Neither shorten to one nor stretch to four.", 30),
                    _L("Madd Muttasil — connected", "جَاءَ  ·  السَّمَاءِ  ·  سُوءَ  ·  جِيءَ",
                       "He came · the sky · evil · it was brought",
                       "Hold a madd letter followed by hamza in the same word for four or five counts.",
                       "Madd muttasil is obligatory (wajib).", 30),
                    _L("Madd Munfasil — separated", "يَا أَيُّهَا  ·  إِنَّا أَعْطَيْنَاكَ  ·  قُوا أَنفُسَكُمْ",
                       "O you · indeed We have granted you · protect yourselves",
                       "Apply four or five counts where the madd ends a word and hamza begins the next.",
                       "Madd munfasil is permissible (ja'iz) — keep one consistent length throughout the recitation.", 30),
                    _L("Madd Lazim — obligatory six counts", "الضَّالِّينَ  ·  الْحَاقَّةُ  ·  آلْآنَ",
                       "Those astray · the Inevitable · now?",
                       "Hold six counts where a madd letter is followed by a sukoon or shaddah.",
                       "The longest madd in the Quran; it is never shortened.", 30),
                    _L("Madd Arid lis-Sukoon", "الرَّحِيمْ  ·  الْعَالَمِينْ  ·  نَسْتَعِينْ",
                       "The Merciful · the worlds · we seek help (when stopping)",
                       "Choose two, four or six counts when stopping on a madd before a temporary sukoon.",
                       "Whichever length is chosen, keep it consistent across the recitation.", 30),
                ]),
                ("Stopping & Starting", "الوقف والابتداء", [
                    _L("The waqf signs", "مـ  ·  لا  ·  ج  ·  صلى  ·  قلى  ·  ۛ ۛ",
                       "Compulsory stop · no stop · permissible stop · continuing is better · stopping is better · embracing stop",
                       "Read and obey the stop marks printed in the mushaf.",
                       "Never stop on مـ-marked continuation; never stop mid-word.", 30),
                    _L("Starting again correctly", "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
                       "In the name of Allah, the Most Gracious, the Most Merciful.",
                       "Resume from a point that preserves the meaning.",
                       "Return to the beginning of the sentence when the meaning would otherwise break.", 25),
                ]),
            ]),
        ],
        "TARJUMA": [
            ("Word-by-Word — Al-Fatiha", "ترجمة الفاتحة", "Vocabulary and meaning of the Opening of the Book.", [
                ("Meaning of Al-Fatiha", "معاني الفاتحة", [
                    _L("Al-Fatiha 1–3 — Praise", "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ\nالْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ\nالرَّحْمَٰنِ الرَّحِيمِ",
                       "1. In the name of Allah, the Most Gracious, the Most Merciful.\n2. All praise belongs to Allah, Lord of all the worlds.\n3. The Most Gracious, the Most Merciful.",
                       "Translate each word of ayat 1–3 and explain الرَّحْمَٰن versus الرَّحِيم.", "", 30, 1, 1, 3),
                    _L("Al-Fatiha 4–5 — Worship", "مَالِكِ يَوْمِ الدِّينِ\nإِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ",
                       "4. Master of the Day of Judgement.\n5. You alone we worship, and You alone we ask for help.",
                       "Explain why إِيَّاكَ comes before the verb and what that word order means.", "", 30, 1, 4, 5),
                    _L("Al-Fatiha 6–7 — Guidance", "اهْدِنَا الصِّرَاطَ الْمُسْتَقِيمَ\nصِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ",
                       "6. Guide us along the straight path.\n7. The path of those You have blessed, not of those who earned anger, nor of those who went astray.",
                       "Translate the three groups of people mentioned in ayah 7.", "", 30, 1, 6, 7),
                ]),
            ]),
            ("Word-by-Word — Short Surahs", "ترجمة قصار السور", "Meaning of the most recited surahs of Juz Amma.", [
                ("Creed Surahs", "سور العقيدة", [
                    surah_full(112, "Meaning of Surah Al-Ikhlas", 30, "Translate every word and explain الصَّمَد."),
                    surah_full(109, "Meaning of Surah Al-Kafirun", 30, "Translate the surah and explain its historical context."),
                ]),
                ("Protection & Gratitude", "سور الاستعاذة والشكر", [
                    surah_full(113, "Meaning of Surah Al-Falaq", 30, "Translate the four evils mentioned in the surah."),
                    surah_full(114, "Meaning of Surah An-Nas", 30, "Translate the three attributes of Allah named in the surah."),
                    surah_full(103, "Meaning of Surah Al-Asr", 30, "Explain the four conditions for escaping loss."),
                ]),
            ]),
        ],
        "ISLAMIC": [
            ("Aqeedah & the Pillars", "العقيدة والأركان", "What a Muslim believes and what a Muslim does.", [
                ("Six Articles of Faith", "أركان الإيمان", [
                    _L("Belief in Allah", "لَا إِلَٰهَ إِلَّا اللَّهُ", "There is no god but Allah.",
                       "State the meaning of the shahadah and the oneness of Allah.", "", 30),
                    _L("Angels, Books and Messengers", "آمَنَ الرَّسُولُ بِمَا أُنزِلَ إِلَيْهِ مِن رَّبِّهِ",
                       "The Messenger believed in what was revealed to him from his Lord.",
                       "Name the angels, the revealed books and the messengers of firm resolve.", "", 30),
                    _L("The Last Day and Divine Decree", "مَالِكِ يَوْمِ الدِّينِ", "Master of the Day of Judgement.",
                       "Explain belief in the Hereafter and in qadar in simple terms.", "", 30),
                ]),
                ("Five Pillars", "أركان الإسلام", [
                    _L("Shahadah and Salah", "أَشْهَدُ أَن لَّا إِلَٰهَ إِلَّا اللَّهُ وَأَشْهَدُ أَنَّ مُحَمَّدًا رَّسُولُ اللَّهِ",
                       "I bear witness that there is no god but Allah and that Muhammad is the Messenger of Allah.",
                       "Recite the testimony of faith and name the five daily prayers.", "", 30),
                    _L("Zakah, Sawm and Hajj", "وَأَقِيمُوا الصَّلَاةَ وَآتُوا الزَّكَاةَ", "And establish prayer and give zakah.",
                       "Explain the remaining three pillars and who they apply to.", "", 30),
                ]),
            ]),
            ("Salah & Purification", "الصلاة والطهارة", "How to pray correctly, step by step.", [
                ("Wudu", "الوضوء", [
                    _L("The steps of wudu", "بِسْمِ اللَّهِ", "In the name of Allah.",
                       "Perform wudu in the correct order without missing an obligatory part.", "", 30),
                    _L("What breaks wudu", "", "", "List the actions that invalidate wudu and when it must be renewed.", "", 25),
                ]),
                ("Salah", "الصلاة", [
                    _L("The prayer step by step", "اللَّهُ أَكْبَرُ  ·  سُبْحَانَ رَبِّيَ الْعَظِيمِ  ·  سُبْحَانَ رَبِّيَ الْأَعْلَىٰ",
                       "Allah is the Greatest · Glory to my Lord, the Magnificent · Glory to my Lord, the Most High",
                       "Perform two rak'ahs with the correct recitation in each posture.", "", 35),
                    _L("Prayer timings and conditions", "إِنَّ الصَّلَاةَ كَانَتْ عَلَى الْمُؤْمِنِينَ كِتَابًا مَّوْقُوتًا",
                       "Indeed, prayer has been decreed upon the believers at specified times.",
                       "Name the five prayers, their times and the number of rak'ahs in each.", "", 30),
                ]),
            ]),
            ("Seerah & Daily Duas", "السيرة والأدعية", "The life of the Prophet ﷺ and the supplications of the day.", [
                ("Seerah", "السيرة النبوية", [
                    _L("Makkah — the early years", "", "", "Narrate the birth, upbringing and first revelation of the Prophet ﷺ.", "", 30),
                    _L("Madinah — the community", "", "", "Narrate the hijrah and the building of the first Muslim community.", "", 30),
                ]),
                ("Daily Duas", "أدعية يومية", [
                    _L("Duas for eating and sleeping", "بِسْمِ اللَّهِ وَعَلَىٰ بَرَكَةِ اللَّهِ  ·  بِاسْمِكَ اللَّهُمَّ أَمُوتُ وَأَحْيَا",
                       "In the name of Allah and with the blessing of Allah · In Your name, O Allah, I die and I live.",
                       "Memorise and use the daily duas for food and sleep.", "", 25),
                    _L("Duas for entering and leaving", "اللَّهُمَّ إِنِّي أَسْأَلُكَ خَيْرَ الْمَوْلِجِ وَخَيْرَ الْمَخْرَجِ",
                       "O Allah, I ask You for the best of entering and the best of leaving.",
                       "Memorise and use the duas for the home and the masjid.", "", 25),
                ]),
            ]),
        ],
    }


# =============================================================================== curriculum builder
def _build_curriculum(db: Session) -> int:
    created = 0
    for code, books in _curriculum().items():
        course = db.query(Course).filter(Course.code == code).first()
        if not course:
            continue
        for b_i, (b_title, b_ar, b_desc, chapters) in enumerate(books):
            book = db.query(Book).filter(Book.course_id == course.id, Book.title == b_title).first()
            if not book:
                book = Book(course_id=course.id, title=b_title, arabic_title=b_ar, description=b_desc, order=b_i)
                db.add(book)
                db.flush()
                created += 1
            for c_i, (c_title, c_ar, lessons) in enumerate(chapters):
                chapter = db.query(Chapter).filter(Chapter.book_id == book.id, Chapter.title == c_title).first()
                if not chapter:
                    chapter = Chapter(book_id=book.id, title=c_title, arabic_title=c_ar, order=c_i)
                    db.add(chapter)
                    db.flush()
                for l_i, data in enumerate(lessons):
                    if db.query(Lesson.id).filter(Lesson.chapter_id == chapter.id, Lesson.title == data["title"]).first():
                        continue
                    db.add(Lesson(chapter_id=chapter.id, order=l_i, **data))
        db.flush()
    return created


def _ensure_versions(db: Session, admin) -> None:
    for course in db.query(Course).all():
        cur = db.query(CurriculumVersion).filter(CurriculumVersion.course_id == course.id, CurriculumVersion.version == "1.1").first()
        if cur:
            continue
        for old in db.query(CurriculumVersion).filter(CurriculumVersion.course_id == course.id):
            old.is_current = False
        db.add(CurriculumVersion(course_id=course.id, version="1.1", is_current=True,
                                 notes="Full lesson content published: books, chapters and lessons with Arabic text and objectives.",
                                 published_by_id=admin.id if admin else None, published_at=datetime.utcnow()))
    db.flush()


# =============================================================================== student data
PLAN_TEMPLATES = [
    ("Sabaq: {lesson} — read with the teacher, then three independent repetitions.", "Recite the previous lesson twice", "Revise the last three lessons"),
    ("New lesson {lesson}: makhaarij drill, teacher model recitation, student repetition ×5.", "Sabqi of yesterday's lesson", "Dor of the current block"),
    ("Continue {lesson}. Focus on madd lengths and stopping at the ayah endings.", "Sabqi: previous two lessons", "Dor: block revision"),
    ("Assessment of {lesson} followed by correction of recurring mistakes.", "Sabqi cycle", "Dor: full memorised portion"),
]
DELIVERED_TEMPLATES = [
    "Delivered {lesson} in full. Student read independently after two teacher models; three repetitions completed.",
    "Covered most of {lesson}. Ran out of time before the final repetitions; makhaarij drill completed.",
    "Delivered {lesson}. Student needed extra correction on madd lengths, so the dor portion was shortened.",
    "{lesson} completed with the planned sabqi and dor. No corrections needed on the last two ayat.",
]
EVAL_COMMENTS = [
    ("Fluency has improved noticeably this month; makhaarij of the throat letters still need daily drill.",
     "Progress is on track for the division. Continue the current pace."),
    ("Strong memorisation, but the ghunnah is being cut short. Two counts must be held consistently.",
     "Recommend an additional weekly Tajweed focus session."),
    ("Excellent recitation with confident stops at the ayah endings. Ready for the next chapter.",
     "Approved for promotion to the next division at the end of the month."),
    ("Attendance gaps have slowed the sabaq. Revision quota was met, but new material is behind plan.",
     "Coordinator to contact the guardian about consistency."),
]
REMARKS = [
    ("Consistent effort throughout the month. Recitation is fluent and confident.", "اس ماہ محنت بہت اچھی رہی۔ تلاوت رواں اور پُراعتماد ہے۔"),
    ("Good memorisation, but Tajweed rules of noon sakinah need more practice.", "حفظ اچھا ہے، البتہ نون ساکن کے قواعد پر مزید مشق درکار ہے۔"),
    ("Excellent performance. Ready to move to the next division.", "کارکردگی بہترین رہی۔ اگلے درجے میں ترقی کے لیے تیار ہیں۔"),
    ("Attendance affected progress this month. Please ensure regular classes.", "اس ماہ حاضری نے پیش رفت متاثر کی۔ باقاعدہ کلاس یقینی بنائیں۔"),
    ("Steady improvement in fluency; revision quota completed on time.", "روانی میں مسلسل بہتری؛ دور کا ہدف بروقت مکمل ہوا۔"),
]
CERT_TITLES = [
    ("Certificate of Completion — Norani Qaida", "QAIDA", "Successfully completed the Norani Qaida foundation programme with correct makhaarij and fluent joining."),
    ("Certificate of Achievement — Juz Amma Memorisation", "HIFZ", "Memorised the short surahs of Juz Amma and passed the oral examination."),
    ("Certificate of Completion — Tajweed Level 1", "TAJWEED", "Completed the rules of noon sakinah, meem sakinah and madd with a distinction grade."),
    ("Certificate of Excellence — Nazra Recitation", "NAZRA", "Achieved fluent, error-free recitation across the assessed portion."),
    ("Certificate of Completion — Islamic Studies Foundation", "ISLAMIC", "Completed the foundation programme in Aqeedah, Salah and Seerah."),
    ("Certificate of Achievement — Word-by-Word Translation", "TARJUMA", "Completed the word-by-word translation of Al-Fatiha and the short surahs."),
]


def _seed_students(db: Session, admin) -> dict:
    from app.services.academic import (course_lessons, generate_monthly_test, issue_certificate, period_of,
                                       score_monthly_test, shift_period, compute_variance, status_for_variance)

    stats = {"progress": 0, "plans": 0, "evaluations": 0, "tests": 0, "certificates": 0, "annotations": 0}
    students = db.query(Student).filter(Student.course_id.isnot(None)).order_by(Student.id).all()
    if not students:
        return stats
    today = date.today()
    prev1, prev2 = shift_period(period_of(), -1), shift_period(period_of(), -2)
    lessons_by_course: dict[int, list] = {}
    cards_made = 0
    certs_made = db.query(Certificate).count()

    for idx, s in enumerate(students):
        rnd = random.Random(f"oqc-academic-{s.id}")
        lessons = lessons_by_course.setdefault(s.course_id, course_lessons(db, s.course_id))
        if not lessons:
            continue
        # ---------------------------------------------------------------- progress
        if not db.query(StudentProgress.id).filter(StudentProgress.student_id == s.id).first():
            months_in = max(0.3, (today - (s.join_date or today)).days / 30.4)
            target = s.course.completion_target_months or 12
            frac = min(0.95, max(0.05, months_in / target * rnd.uniform(0.7, 1.25)))
            n_done = max(1, int(len(lessons) * frac))
            for i, les in enumerate(lessons[:n_done + 1]):
                completed = i < n_done
                started = datetime.combine(s.join_date or today, datetime.min.time()) + timedelta(days=i * 3, hours=16)
                db.add(StudentProgress(
                    student_id=s.id, lesson_id=les.id, teacher_id=s.teacher_id,
                    status="completed" if completed else "in_progress",
                    progress_type="sabaq" if s.course.code != "HIFZ" else rnd.choice(["sabaq", "sabaq", "sabqi"]),
                    started_at=started, completed_at=started + timedelta(hours=1) if completed else None,
                    score=round(rnd.uniform(6.5, 9.8), 1) if completed else None,
                    notes=None if completed else "In progress — current sabaq."))
                stats["progress"] += 1
            s.current_lesson_id = lessons[min(n_done, len(lessons) - 1)].id
            cur = lessons[min(n_done, len(lessons) - 1)]
            if not s.sabaq_position:
                s.sabaq_position = f"{cur.chapter.book.title if cur.chapter and cur.chapter.book else ''} — {cur.title}"[:120]
            db.flush()

        cur_lesson = db.get(Lesson, s.current_lesson_id) if s.current_lesson_id else lessons[0]

        # ---------------------------------------------------------------- lesson plans (last 3 weeks)
        if not db.query(LessonPlan.id).filter(LessonPlan.student_id == s.id).first():
            for k in range(rnd.randint(2, 4)):
                d = today - timedelta(days=rnd.randint(1, 21))
                tmpl = PLAN_TEMPLATES[(s.id + k) % len(PLAN_TEMPLATES)]
                planned = tmpl[0].format(lesson=cur_lesson.title)
                plan = LessonPlan(student_id=s.id, teacher_id=s.teacher_id, plan_date=d,
                                  plan_type="weekly" if k == 0 and rnd.random() < 0.35 else "daily",
                                  lesson_id=cur_lesson.id, planned_content=planned,
                                  sabaq=cur_lesson.title[:200], sabqi=tmpl[1][:200], dor=tmpl[2][:200],
                                  next_objectives=cur_lesson.objectives, status="planned")
                if d < today - timedelta(days=1) or rnd.random() < 0.75:
                    delivered = DELIVERED_TEMPLATES[(s.id + k) % len(DELIVERED_TEMPLATES)].format(lesson=cur_lesson.title)
                    plan.delivered_content = delivered
                    plan.variance_pct = compute_variance(planned, delivered)
                    plan.status = status_for_variance(plan.variance_pct)
                    plan.teacher_notes = "Recitation recorded; correction list shared with the guardian." if rnd.random() < 0.4 else None
                    if rnd.random() < 0.4:
                        plan.reviewed_by_id = admin.id if admin else None
                        plan.reviewed_at = datetime.utcnow() - timedelta(days=rnd.randint(1, 5))
                        plan.review_comment = rnd.choice([
                            "Reviewed — planned content matches delivery. No action needed.",
                            "Variance acceptable. Ask the teacher to shorten the dor portion next week.",
                            "Delivery gap noted; coordinator to observe the next session."])
                db.add(plan)
                stats["plans"] += 1
            db.flush()

        # ---------------------------------------------------------------- evaluations
        if not db.query(Evaluation.id).filter(Evaluation.student_id == s.id).first():
            for k in range(rnd.randint(1, 3)):
                d = today - timedelta(days=rnd.randint(5, 80))
                crit = {"tajweed": rnd.randint(5, 10), "fluency": rnd.randint(5, 10),
                        "memorisation": rnd.randint(4, 10), "understanding": rnd.randint(5, 10)}
                score = round(sum(crit.values()) / len(crit) * 10, 1)
                tc, ac = EVAL_COMMENTS[(s.id + k) % len(EVAL_COMMENTS)]
                db.add(Evaluation(student_id=s.id, teacher_id=s.teacher_id,
                                  evaluation_type=rnd.choice(["weekly", "manual", "monthly", "level_completion"]),
                                  date=d, score=score, max_score=100, result="pass" if score >= 55 else "fail",
                                  criteria=crit, teacher_comment=tc, academic_comment=ac if rnd.random() < 0.5 else None,
                                  reviewed_by_id=admin.id if admin and rnd.random() < 0.4 else None))
                stats["evaluations"] += 1
            db.flush()

        # ---------------------------------------------------------------- monthly tests (previous two months)
        if s.status in ("active", "trial") and not db.query(MonthlyTest.id).filter(MonthlyTest.student_id == s.id).first():
            base = rnd.uniform(0.55, 0.95)
            for j, period in enumerate((prev2, prev1)):
                test = generate_monthly_test(db, s, period)
                test.created_at = datetime.utcnow() - timedelta(days=60 - j * 30)
                factor = base if j == 0 else min(0.99, max(0.35, base + rnd.uniform(-0.18, 0.16)))
                scores = {str(i): round(q["max"] * min(1.0, max(0.2, factor + rnd.uniform(-0.12, 0.12))), 1)
                          for i, q in enumerate(test.questions or [])}
                en, ur = REMARKS[(s.id + j) % len(REMARKS)]
                make_card = cards_made < 5 and j == 1
                score_monthly_test(db, test, scores, en, ur, admin, generate_card=make_card,
                                   scored_at=datetime.utcnow() - timedelta(days=55 - j * 30))
                if make_card:
                    cards_made += 1
                    test.status = "delivered"
                    test.delivered_at = datetime.utcnow() - timedelta(days=54 - j * 30)
                    test.delivery_channels = ["in_app", "whatsapp"]
                stats["tests"] += 1
            db.flush()

        # ---------------------------------------------------------------- dor progress for the current month
        sched = (db.query(DorSchedule).filter(DorSchedule.student_id == s.id, DorSchedule.period == period_of())
                 .order_by(DorSchedule.id.desc()).first())
        if sched and not sched.completed and sched.items:
            done_n = sched.quota if rnd.random() < 0.6 else rnd.randint(0, max(0, sched.quota - 1))
            items = [dict(x) for x in sched.items]
            for k in range(min(done_n, len(items))):
                items[k]["done"] = True
                items[k]["done_at"] = (datetime.utcnow() - timedelta(days=rnd.randint(1, 20))).isoformat()
            sched.items = items
            sched.completed = sum(1 for x in items if x.get("done"))
            sched.quota_met = sched.completed >= sched.quota
            s.dor_quota_met = sched.quota_met
            stats["dor_items"] = stats.get("dor_items", 0) + sched.completed
            db.flush()

        # ---------------------------------------------------------------- certificates (~6 in total, with PDFs)
        if certs_made < 6 and idx % 7 == 3:
            title, code, desc = CERT_TITLES[certs_made % len(CERT_TITLES)]
            course = db.query(Course).filter(Course.code == code).first()
            if not db.query(Certificate.id).filter(Certificate.student_id == s.id, Certificate.title == title).first():
                issue_certificate(db, s, course or s.course, title, admin, generation="manual" if certs_made % 2 else "automatic",
                                  description=desc, issued_at=today - timedelta(days=rnd.randint(10, 200)))
                certs_made += 1
                stats["certificates"] += 1
                db.flush()

        # ---------------------------------------------------------------- a couple of shared-view annotations
        if idx % 9 == 2 and cur_lesson and cur_lesson.arabic_text and \
                not db.query(LessonAnnotation.id).filter(LessonAnnotation.student_id == s.id).first():
            teacher_user_id = s.teacher.user_id if s.teacher else None
            for w, (atype, note, colour) in enumerate([
                ("mistake", "Ghunnah cut short here — hold two full counts.", "#f43f5e"),
                ("tajweed", "Ikhfa: hide the noon and prepare the next letter.", "#7c3aed"),
                ("highlight", "Read this word three times before the next class.", "#facc15"),
            ]):
                db.add(LessonAnnotation(lesson_id=cur_lesson.id, student_id=s.id, author_id=teacher_user_id or (admin.id if admin else None),
                                        word_index=w, annotation_type=atype, color=colour, note=note))
                stats["annotations"] += 1
            db.flush()
    return stats


# =============================================================================== entry point
_DEFERRED_ARMED = False
_STUDENT_SEED_DONE = False


def _run_student_seed(db: Session) -> dict:
    global _STUDENT_SEED_DONE
    admin = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    stats = _seed_students(db, admin)
    db.commit()
    _STUDENT_SEED_DONE = True
    return stats


def _arm_deferred() -> None:
    """The seed runner executes modules in the order core, academic, people, ... — students therefore do not
    exist yet when this module is called. Defer the per-student academic data to the end of the seed process."""
    global _DEFERRED_ARMED
    if _DEFERRED_ARMED:
        return
    _DEFERRED_ARMED = True
    import atexit

    def _finish() -> None:
        if _STUDENT_SEED_DONE:
            return  # the seed runner already executed the student stage explicitly
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            if not db.query(Student.id).filter(Student.course_id.isnot(None)).first():
                return
            stats = _run_student_seed(db)
            print("  + seed.academic_curriculum (students): " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        except Exception as exc:  # pragma: no cover - seeding must never break the run
            db.rollback()
            print(f"  ! seed.academic_curriculum (students) failed: {exc}")
        finally:
            db.close()

    atexit.register(_finish)


def run(db: Session) -> None:
    admin = db.query(User).filter(User.is_superuser.is_(True)).order_by(User.id).first()
    _build_curriculum(db)
    db.flush()
    _ensure_versions(db, admin)
    db.commit()
    if db.query(Student.id).filter(Student.course_id.isnot(None)).first():
        _run_student_seed(db)
    else:
        _arm_deferred()
