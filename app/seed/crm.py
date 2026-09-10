"""CRM & Growth seed: lead sources, campaigns, leads, conversations, templates, sequences, trials,
surveys & feedback, cases, ambassador referrals and retention actions. Idempotent, fixed random seed."""
from __future__ import annotations

import random
from datetime import datetime, date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import sign_value
from app.models.academic import Course
from app.models.core import User, Setting, Department
from app.models.crm import (LEAD_STAGES, Lead, LeadActivity, LeadSource, Campaign, CampaignMetric, Conversation, Message,
                            InternalNote, MessageTemplate, Sequence, SequenceEnrollment, Referral, Survey, Feedback,
                            Case, CaseComment, RetentionAction)
from app.models.people import Client, Student, Teacher, Employee
from app.models.scheduling import Trial
from app.services import crm as svc
from app.services import retention as retention_svc

SOURCES = [("WhatsApp", "inbound"), ("Website", "inbound"), ("Meta Ads", "paid"), ("Google Ads", "paid"),
           ("Referral", "referral"), ("Manual", "manual"), ("GHL", "integration")]

TEMPLATES = [
    ("lead_first_touch", "follow_up", "Assalamu Alaikum {{name}}, this is Online Quran College. Thank you for your interest in Quran classes for {{student}}. May I book a free trial class this week, in sha Allah?"),
    ("lead_follow_up_2", "follow_up", "Assalamu Alaikum {{name}}, just following up about {{student}}'s free trial. We have evening slots available. Which day suits you best?"),
    ("trial_reminder", "reminder", "Reminder: {{student}}'s free trial class with {{teacher}} starts soon. Join here: {{link}}. JazakAllah Khair."),
    ("trial_follow_up", "trial", "Assalamu Alaikum {{name}}, how was {{student}}'s trial class? We would love to continue the journey together. Reply YES to enrol."),
    ("win_back", "retention", "Assalamu Alaikum {{name}}, we noticed {{student}} has missed a few classes. Can we help with a different timing or a short refresher plan?"),
    ("payment_reminder", "billing", "Dear {{name}}, this is a gentle reminder about your Online Quran College invoice. You can pay securely here: {{link}}."),
    ("ambassador_invite", "referral", "Assalamu Alaikum {{name}}, families like yours make our college. Share your link {{link}} - both families receive a free week when a new student enrols."),
    ("feedback_survey", "feedback", "Assalamu Alaikum {{name}}, please share 1-minute feedback about {{student}}'s classes: {{link}}"),
    ("pre_leave_offer", "retention", "Assalamu Alaikum {{name}}, before you decide, may we offer {{student}} a free week and a teacher who matches your timing better?"),
    ("freeze_reactivation", "retention", "Assalamu Alaikum {{name}}, {{student}}'s freeze ends soon. Shall we reserve the same slot with {{teacher}}?"),
]

SEQUENCES = [
    ("Lead follow-up - 4 touches", "lead_follow_up",
     [{"day": 0, "template": "lead_first_touch"}, {"day": 2, "template": "lead_follow_up_2"},
      {"day": 5, "body": "Assalamu Alaikum {{name}}, our evening slots for {{student}} are filling up. Shall I hold one for you?"},
      {"day": 9, "body": "Assalamu Alaikum {{name}}, this is my last follow-up. Reply any time and we will book {{student}}'s free trial."}]),
    ("Trial follow-up", "trial_follow_up",
     [{"day": 0, "template": "trial_follow_up"},
      {"day": 2, "body": "Assalamu Alaikum {{name}}, the teacher shared a lovely report about {{student}}. Shall we continue with a monthly plan?"},
      {"day": 6, "body": "Assalamu Alaikum {{name}}, we can still hold {{student}}'s slot until tomorrow, in sha Allah."}]),
    ("Win-back - at-risk families", "win_back",
     [{"day": 0, "template": "win_back"},
      {"day": 3, "body": "Assalamu Alaikum {{name}}, would a different teacher or an earlier time help {{student}} attend more regularly?"},
      {"day": 7, "body": "Assalamu Alaikum {{name}}, we would love to have {{student}} back. May I call you today?"}]),
    ("Pre-leave offer", "pre_leave_offer", [{"day": 0, "template": "pre_leave_offer"},
                                            {"day": 3, "body": "Assalamu Alaikum {{name}}, the free week offer for {{student}} is still open until Friday."}]),
    ("Freeze reactivation", "freeze_reactivation", [{"day": 0, "template": "freeze_reactivation"},
                                                    {"day": 4, "body": "Assalamu Alaikum {{name}}, shall we restart {{student}}'s classes next week, in sha Allah?"}]),
    ("Payment reminders", "payment_reminder", [{"day": 0, "template": "payment_reminder"},
                                               {"day": 4, "body": "Dear {{name}}, your invoice is still outstanding. Please let us know if you need a payment plan."}]),
]

CAMPAIGNS = [
    ("UK Hifz - Ramadan offer", "meta", "Lead generation", 4500, "GBP", "United Kingdom", "2 free trial classes", "facebook", "uk-hifz-ramadan", "cmp_meta_uk_01"),
    ("US Nazra - evening slots", "meta", "Lead generation", 3800, "USD", "United States", "First week free", "instagram", "us-nazra-evening", "cmp_meta_us_02"),
    ("Google Search - learn Quran online", "google", "Search intent", 5200, "USD", "United States", "Free assessment", "google", "search-learn-quran", "cmp_goog_03"),
    ("Canada Tajweed - weekend", "google", "Search intent", 1900, "CAD", "Canada", "Weekend batch", "google", "ca-tajweed-weekend", "cmp_goog_04"),
    ("Ambassador referral programme", "referral", "Word of mouth", 600, "GBP", None, "Free week for both families", "referral", "ambassador-2026", "cmp_ref_05"),
]

COUNTRIES = [("United Kingdom", "+44"), ("United States", "+1"), ("Canada", "+1"), ("Australia", "+61"),
             ("Ireland", "+353"), ("Germany", "+49"), ("Pakistan", "+92")]
FIRST = ["Ahmed", "Fatima", "Bilal", "Ayesha", "Yusuf", "Khadija", "Omar", "Zainab", "Hamza", "Maryam", "Ibrahim",
         "Safia", "Tariq", "Nadia", "Adam", "Sumayya", "Idris", "Ruqayya", "Musa", "Asma"]
LAST = ["Khan", "Ali", "Hussain", "Patel", "Rahman", "Malik", "Siddiqui", "Chowdhury", "Farooq", "Aziz", "Iqbal",
        "Mahmood", "Sheikh", "Baig", "Ansari", "Qureshi"]
LOST_REASONS = ["Price too high", "Timing did not suit", "Chose a local madrasa", "Went unresponsive",
                "Wanted a female teacher only", "Postponed to next term"]
NOTE_SNIPPETS = [
    "Father called back, wants weekend classes for two children.",
    "Mother prefers a female teacher for the daughter.",
    "Family is comparing us with another academy; sent the curriculum PDF.",
    "Asked about Hifz timelines and monthly test reports.",
    "Wants a 15-minute demo before committing.",
    "Budget conscious - explained the sibling discount ladder.",
]

THREADS = [
    [("in", "Assalamu Alaikum, I saw your advert. How much are the Quran classes?"),
     ("out", "Wa Alaikum Assalam {name}. JazakAllah Khair for reaching out. Classes are one-to-one, 30 minutes, five days a week. May I book a free trial for {student}?"),
     ("in", "Ji han, please. Evening time theek rahe ga."),
     ("out", "Perfect. I have Monday 6:30pm UK time with Qari sahib. Shall I confirm that slot?")],
    [("in", "السلام عليكم، مجھے اپنے بیٹے کے لیے قرآن کی کلاس چاہیے"),
     ("out", "وعليكم السلام {name}. JazakAllah Khair. We teach Qaida, Nazra, Hifz and Tajweed one-to-one. Kya main {student} ke liye free trial book kar doon?"),
     ("in", "Bilkul, kal sham 7 baje theek rahega."),
     ("out", "Booked, in sha Allah. Aap ko class se pehle reminder aur joining link mil jaye ga.")],
    [("out", "Assalamu Alaikum {name}, this is Online Quran College. How was {student}'s trial class today?"),
     ("in", "MashaAllah it was very good, the teacher was patient."),
     ("out", "Alhamdulillah. Shall we continue with three classes a week? I can hold the same slot for you."),
     ("in", "Yes please, send the details.")],
    [("out", "Assalamu Alaikum {name}, we noticed {student} missed two classes this week. Is everything okay?"),
     ("in", "Sorry, exams chal rahe hain. Do hafte baad restart kar sakte hain?"),
     ("out", "Of course. I will freeze the schedule for two weeks and reserve the same teacher, in sha Allah.")],
    [("in", "Invoice ke baare mein baat karni thi."),
     ("out", "Wa Alaikum Assalam {name}. Certainly - our billing team will call you within the hour. Would you like a monthly or termly plan?"),
     ("in", "Monthly better hai. JazakAllah Khair.")],
]

INTERNAL_NOTES = ["Warm family - prioritise the callback.", "Price sensitive; sibling discount already explained.",
                  "Prefers Urdu conversation; assign an Urdu-speaking closer.", "Do not call before 5pm local time."]

CASE_SEEDS = [
    ("complaint", "Teacher joined 10 minutes late twice this week", "The class is scheduled at 6:30pm but the teacher joined at 6:40pm on Monday and Wednesday. My son lost half of his lesson time.", "punctuality"),
    ("complaint", "Audio kept cutting during the class", "The connection was poor and my daughter could not hear the tajweed corrections properly.", "technical"),
    ("billing", "Charged twice for the same month", "Two payments were taken in March. Please refund one of them.", "billing"),
    ("request", "Change class timing to weekends", "Weekday evenings clash with school homework. Can we move to Saturday and Sunday mornings?", "schedule"),
    ("teacher_change", "Request a female teacher for our daughter", "Our daughter is 11 and we would prefer a female teacher, jazakAllah khair.", "teaching_quality"),
    ("technical", "Cannot open the class link on the tablet", "The join button does nothing on the iPad but works on the laptop.", "technical"),
    ("complaint", "Progress has been slow this term", "My son is still on the same lesson after five weeks. We expected faster progress.", "teaching_quality"),
    ("schedule_change", "Need to shift classes by 30 minutes", "Please move the class from 7pm to 7:30pm from next week.", "schedule"),
    ("feedback", "Very happy with the new teacher", "MashaAllah the new teacher is excellent and my daughter looks forward to class.", "teaching_quality"),
    ("request", "Result card for last month", "We did not receive the monthly result card for our son.", "general"),
]

ROOT_CAUSES = ["Teacher schedule overlap", "ISP outage at teacher location", "Duplicate payment gateway retry",
               "Slot capacity in the requested shift", "Gender preference not captured at enrolment",
               "Browser compatibility on tablet", "Lesson plan not updated after monthly test"]

RESOLUTIONS = ["Teacher counselled, punctuality monitored for 30 days and the missed minutes were made up.",
               "Teacher moved to the backup connection and QA re-sampled the next three classes.",
               "Duplicate payment refunded and the gateway retry rule was fixed.",
               "Schedule moved to the weekend batch with the same teacher.",
               "Female teacher assigned from the following week.",
               "Tablet browser issue resolved with the new classroom build.",
               "Lesson plan reset with the academics team and a revision plan shared with the family."]

SURVEYS = [
    ("Post parent-teacher meeting pulse", "post_ptm", "client"),
    ("Monthly result card feedback", "post_result_card", "client"),
    ("30-day welcome check-in", "tenure_30", "client"),
    ("90-day family experience", "tenure_90", "client"),
    ("Staff engagement pulse (eNPS)", "staff_enps", "staff"),
]

DEFAULT_QUESTIONS = [{"key": "nps", "type": "nps", "text": "How likely are you to recommend Online Quran College to a friend or family member?"},
                     {"key": "rating", "type": "rating", "text": "How would you rate the classes this month?"},
                     {"key": "comment", "type": "text", "text": "What could we do better?"}]
STAFF_QUESTIONS = [{"key": "nps", "type": "nps", "text": "How likely are you to recommend Online Quran College as a place to work?"},
                   {"key": "comment", "type": "text", "text": "What would make your work here better? (confidential)"}]

POSITIVE_COMMENTS = ["MashaAllah the teacher is excellent and very patient with my son.",
                     "Great experience, my daughter loves her classes and her tajweed has improved.",
                     "JazakAllah Khair, the monthly result card is very helpful.",
                     "Very professional, the reminders and reports are excellent."]
NEUTRAL_COMMENTS = ["Classes are good overall, timing could be a little more flexible.",
                    "Happy so far, would like more feedback after each class.",
                    "It is fine, we are still settling into the routine."]
NEGATIVE_COMMENTS = ["The teacher has been late several times and progress is slow.",
                     "We are not happy with the audio quality and the class was missed twice.",
                     "Billing was confusing this month and nobody called us back.",
                     "My son is not enjoying the classes, the teacher is not engaging."]
STAFF_COMMENTS = ["Workload in the evening shift is heavy but the team is supportive.",
                  "More clarity on the promotion path would help.",
                  "Good culture, alhamdulillah. Salary review would be appreciated.",
                  "Too many last-minute schedule changes from operations."]


# ----------------------------------------------------------------------------- helpers
def _phone(rnd: random.Random, prefix: str) -> str:
    return f"{prefix}{rnd.randint(7000000000, 7999999999)}"[:14]


def _setting(db: Session, key: str, value: dict, group: str, desc: str) -> None:
    if not db.query(Setting).filter(Setting.key == key).first():
        db.add(Setting(key=key, value=value, group=group, description=desc))


# ----------------------------------------------------------------------------- sections
def seed_sources(db: Session) -> dict[str, LeadSource]:
    for name, kind in SOURCES:
        if not db.query(LeadSource).filter(LeadSource.name == name).first():
            db.add(LeadSource(name=name, source_type=kind, is_active=True))
    db.flush()
    return {s.name: s for s in db.query(LeadSource).all()}


def seed_templates(db: Session) -> None:
    for name, category, body in TEMPLATES:
        if not db.query(MessageTemplate).filter(MessageTemplate.name == name).first():
            db.add(MessageTemplate(name=name, category=category, channel="whatsapp", language="en", body=body, is_approved=True))
    db.flush()


def seed_sequences(db: Session) -> list[Sequence]:
    for name, stype, steps in SEQUENCES:
        if not db.query(Sequence).filter(Sequence.name == name).first():
            db.add(Sequence(name=name, sequence_type=stype, channel="whatsapp", steps=steps, is_active=True))
    db.flush()
    return db.query(Sequence).order_by(Sequence.id).all()


def seed_campaigns(db: Session, rnd: random.Random) -> list[Campaign]:
    today = date.today()
    for name, platform, objective, budget, currency, country, offer, utm_src, utm_cmp, ext in CAMPAIGNS:
        c = db.query(Campaign).filter(Campaign.name == name).first()
        if not c:
            c = Campaign(name=name, platform=platform, objective=objective, budget=budget, currency=currency, country=country,
                         offer=offer, utm_source=utm_src, utm_campaign=utm_cmp, external_id=ext, status="active",
                         start_date=today - timedelta(days=70), end_date=today + timedelta(days=30))
            db.add(c)
            db.flush()
        if db.query(func.count(CampaignMetric.id)).filter(CampaignMetric.campaign_id == c.id).scalar():
            continue
        total = 0.0
        for i in range(60, 0, -1):
            d = today - timedelta(days=i)
            base = 1.0 if platform != "referral" else 0.15
            impressions = int(rnd.uniform(1500, 9000) * base)
            clicks = int(impressions * rnd.uniform(0.012, 0.05))
            leads = max(0, int(clicks * rnd.uniform(0.05, 0.18)))
            spend = round(clicks * rnd.uniform(0.35, 1.4), 2)
            conversions = max(0, int(leads * rnd.uniform(0.1, 0.35)))
            revenue = round(conversions * rnd.uniform(35, 90), 2)
            total += spend
            db.add(CampaignMetric(campaign_id=c.id, date=d, impressions=impressions, clicks=clicks, leads=leads,
                                  spend=spend, conversions=conversions, revenue=revenue))
        c.spend = round(total, 2)
    db.flush()
    return db.query(Campaign).order_by(Campaign.id).all()


def seed_leads(db: Session, rnd: random.Random, sources: dict, campaigns: list[Campaign]) -> list[Lead]:
    existing = db.query(func.count(Lead.id)).scalar() or 0
    if existing >= 70:
        return db.query(Lead).order_by(Lead.id).all()
    courses = db.query(Course).order_by(Course.id).all()
    closers = svc.closers(db)
    generators = svc.users_with_role(db, "lead_generator")
    stage_weights = [("new", 14), ("contacted", 16), ("trial_scheduled", 10), ("trial_done", 8),
                     ("negotiation", 7), ("won", 20), ("lost", 15)]
    stages = []
    for stage, n in stage_weights:
        stages.extend([stage] * n)
    rnd.shuffle(stages)
    src_names = ["WhatsApp", "Website", "Meta Ads", "Meta Ads", "Google Ads", "Referral", "Manual", "GHL"]
    made: list[Lead] = []
    for i, stage in enumerate(stages):
        country, prefix = COUNTRIES[i % len(COUNTRIES)]
        first, last = FIRST[i % len(FIRST)], LAST[(i * 3) % len(LAST)]
        name = f"{first} {last}"
        src_name = src_names[i % len(src_names)]
        campaign = None
        if src_name == "Meta Ads":
            campaign = campaigns[0] if country == "United Kingdom" else campaigns[1]
        elif src_name == "Google Ads":
            campaign = campaigns[2] if country == "United States" else campaigns[3]
        elif src_name == "Referral":
            campaign = campaigns[4]
        created = datetime.utcnow() - timedelta(days=rnd.randint(1, 90), hours=rnd.randint(0, 20))
        phone = _phone(rnd, prefix)
        # a few deliberate duplicates so the duplicate-detection UI has data
        if i in (7, 23, 51) and made:
            phone = made[i - 5].phone
        data = {"full_name": name, "email": f"{first.lower()}.{last.lower()}{i}@example.com", "phone": phone, "whatsapp": phone,
                "country": country, "student_name": f"{FIRST[(i * 5) % len(FIRST)]} {last}", "student_age": rnd.randint(5, 16),
                "students_count": 2 if i % 7 == 0 else 1, "course_interest_id": courses[i % len(courses)].id if courses else None,
                "preferred_time": rnd.choice(["Weekday evenings", "Weekend mornings", "After Maghrib", "Early morning"]),
                "source_id": sources[src_name].id if src_name in sources else None,
                "campaign_id": campaign.id if campaign else None, "stage": "new", "created_at": created,
                "generator_id": generators[i % len(generators)].id if generators else None,
                "notes": rnd.choice(NOTE_SNIPPETS) if i % 3 == 0 else None}
        lead, _dups = svc.create_lead(db, data, actor=None, auto_assign=True)
        # walk the lead forward through its stage history
        if stage != "new":
            for k, act in enumerate(["call", "whatsapp", "note"][: rnd.randint(1, 3)]):
                svc.add_activity(db, lead, act, rnd.choice(NOTE_SNIPPETS), None,
                                 when=created + timedelta(hours=rnd.randint(1, 60) + k * 6))
            lead.stage = stage
            lead.last_contacted_at = created + timedelta(hours=rnd.randint(1, 48))
            if stage == "lost":
                lead.lost_reason = rnd.choice(LOST_REASONS)
            if stage == "won":
                lead.converted_at = created + timedelta(days=rnd.randint(2, 20))
            if stage not in ("won", "lost"):
                lead.next_follow_up = datetime.utcnow() + timedelta(days=rnd.randint(-3, 6), hours=rnd.randint(0, 8))
            db.add(LeadActivity(lead_id=lead.id, activity_type="stage_change", note=f"Stage new -> {stage}",
                                user_id=lead.assigned_to_id, created_at=created + timedelta(hours=rnd.randint(2, 72))))
        made.append(lead)
    # link ~20 won leads to existing seeded clients (no duplicate data entry)
    won = [l for l in made if l.stage == "won"][:20]
    clients = db.query(Client).filter(Client.lead_id.is_(None)).order_by(Client.id).limit(len(won)).all()
    for lead, client in zip(won, clients):
        lead.converted_client_id = client.id
        lead.converted_at = lead.converted_at or (lead.created_at + timedelta(days=5))
        client.lead_id = lead.id
        client.source = lead.source.name if lead.source else client.source
        db.add(LeadActivity(lead_id=lead.id, activity_type="stage_change", user_id=lead.assigned_to_id,
                            note=f"Converted to client {client.client_code}", created_at=lead.converted_at))
    db.flush()
    return made


def seed_conversations(db: Session, rnd: random.Random, leads: list[Lead]) -> None:
    if db.query(func.count(Conversation.id)).scalar():
        return
    staff = svc.closers(db) + svc.users_with_role(db, "lead_generator") + svc.users_with_role(db, "billing_rep")
    contacts: list[tuple[str, object]] = []
    for l in leads[:18]:
        if l.phone:
            contacts.append(("lead", l))
    for c in db.query(Client).order_by(Client.id).limit(14).all():
        contacts.append(("client", c))
    for i, (ctype, contact) in enumerate(contacts):
        conv = svc.get_or_create_conversation(db, ctype, contact)
        conv.assigned_to_id = staff[i % len(staff)].id if staff else None
        conv.status = ["open", "open", "pending", "closed"][i % 4]
        conv.tags = [ctype] + (["hot"] if i % 5 == 0 else []) + (["billing"] if ctype == "client" and i % 4 == 0 else [])
        thread = THREADS[i % len(THREADS)]
        base = datetime.utcnow() - timedelta(days=rnd.randint(1, 25), hours=rnd.randint(1, 12))
        student = getattr(contact, "student_name", None) or (contact.students[0].full_name if getattr(contact, "students", None) else "your child")
        last = None
        for k, (direction, body) in enumerate(thread):
            text = body.replace("{name}", contact.full_name.split()[0]).replace("{student}", student)
            m = Message(conversation_id=conv.id, direction=direction, body=text, message_type="text",
                        status="read" if direction == "out" else "delivered",
                        sender_id=conv.assigned_to_id if direction == "out" else None,
                        created_at=base + timedelta(minutes=12 * k))
            db.add(m)
            last = m
        if i % 6 == 3:
            db.add(Message(conversation_id=conv.id, direction="out", body="Assalamu Alaikum, following up on the below.",
                           message_type="text", status="failed", error="Simulated provider error (rate limited)",
                           sender_id=conv.assigned_to_id, created_at=base + timedelta(hours=6)))
        db.flush()
        if last is not None:
            conv.last_message_at = last.created_at
            conv.last_message_preview = last.body[:200]
            conv.unread_count = 1 if last.direction == "in" else 0
        if i % 3 == 0 and staff:
            db.add(InternalNote(conversation_id=conv.id, user_id=staff[i % len(staff)].id,
                                text=rnd.choice(INTERNAL_NOTES), created_at=base + timedelta(hours=1)))
    db.flush()


def seed_enrollments(db: Session, rnd: random.Random, leads: list[Lead]) -> None:
    if db.query(func.count(SequenceEnrollment.id)).scalar():
        return
    open_leads = [l for l in leads if l.stage in ("new", "contacted", "trial_scheduled")][:18]
    for i, l in enumerate(open_leads):
        e = svc.enroll_sequence(db, "lead_follow_up", "lead", l.id, enrolled_by="system")
        if e and i % 4 == 0:
            e.status, e.stop_reason, e.next_run_at = "replied", "Contact replied", None
        elif e and i % 5 == 0:
            e.status, e.next_run_at = "completed", None
            e.current_step = len(e.sequence.steps or [])
    for l in [x for x in leads if x.stage == "trial_done"][:6]:
        svc.enroll_sequence(db, "trial_follow_up", "lead", l.id, enrolled_by="system")
    db.flush()


def seed_trials(db: Session, rnd: random.Random, leads: list[Lead]) -> None:
    if db.query(func.count(Trial.id)).scalar() >= 30:
        return
    teachers = db.query(Teacher).filter(Teacher.status == "active").order_by(Teacher.id).all()
    taken = {t[0] for t in db.query(Trial.lead_id).filter(Trial.lead_id.isnot(None)).all()}
    pool = [l for l in leads if l.stage in ("trial_scheduled", "trial_done", "negotiation", "won", "lost") and l.id not in taken][:30]
    statuses = ["scheduled"] * 6 + ["attended"] * 8 + ["no_show"] * 4 + ["converted"] * 8 + ["lost"] * 4
    for i, lead in enumerate(pool):
        status = statuses[i % len(statuses)]
        teacher = teachers[i % len(teachers)] if teachers else None
        when = datetime.utcnow() + timedelta(days=rnd.randint(1, 8)) if status == "scheduled" else \
            datetime.utcnow() - timedelta(days=rnd.randint(1, 60), hours=rnd.randint(0, 10))
        tr = Trial(lead_id=lead.id, client_id=lead.converted_client_id, teacher_id=teacher.id if teacher else None,
                   course_id=lead.course_interest_id, student_name=lead.student_name or lead.full_name,
                   scheduled_at=when, status=status, closer_id=lead.assigned_to_id,
                   follow_up_date=(when.date() + timedelta(days=1)) if status != "scheduled" else None,
                   follow_up_count=0 if status == "scheduled" else rnd.randint(0, 3),
                   outcome={"attended": "Parent impressed, deciding on the plan", "no_show": "Family did not join, rebooking",
                            "converted": "Enrolled on a monthly plan", "lost": "Chose another provider",
                            "scheduled": None}[status],
                   teacher_feedback=None if status in ("scheduled", "no_show") else
                   rnd.choice(["Student can identify most letters; ready for Qaida lesson 4.",
                               "Good recitation, needs work on madd rules.",
                               "Very attentive child, recommend three classes a week."]))
        db.add(tr)
        db.flush()
        if status in ("attended", "converted", "lost"):
            svc.add_activity(db, lead, "trial", f"Trial {status}: {tr.outcome}", None, when=when + timedelta(hours=1))
    db.flush()


def seed_surveys_feedback(db: Session, rnd: random.Random) -> None:
    for name, trigger, audience in SURVEYS:
        if not db.query(Survey).filter(Survey.name == name).first():
            db.add(Survey(name=name, trigger=trigger, audience=audience, is_active=True,
                          questions=STAFF_QUESTIONS if audience == "staff" else DEFAULT_QUESTIONS))
    db.flush()
    if db.query(func.count(Feedback.id)).scalar():
        return
    surveys = {s.trigger: s for s in db.query(Survey).all()}
    client_surveys = [s for s in db.query(Survey).filter(Survey.audience == "client").order_by(Survey.id).all()]
    clients = db.query(Client).order_by(Client.id).all()
    qa_hod = svc.user_by_email(db, "qa@oqc.local")
    for i in range(60):
        client = clients[i % len(clients)] if clients else None
        if not client:
            break
        survey = client_surveys[i % len(client_surveys)] if client_surveys else None
        student = client.students[0] if client.students else None
        submitted = datetime.utcnow() - timedelta(days=rnd.randint(1, 150), hours=rnd.randint(0, 12))
        bucket = i % 10
        if bucket <= 4:
            nps, rating, comment = rnd.randint(9, 10), rnd.randint(4, 5), rnd.choice(POSITIVE_COMMENTS)
        elif bucket <= 7:
            nps, rating, comment = rnd.randint(7, 8), 4, rnd.choice(NEUTRAL_COMMENTS)
        else:
            nps, rating, comment = rnd.randint(0, 6), rnd.randint(1, 2), rnd.choice(NEGATIVE_COMMENTS)
        fb = Feedback(survey_id=survey.id if survey else None, trigger=survey.trigger if survey else "manual",
                      respondent_type="client", client_id=client.id, student_id=student.id if student else None,
                      teacher_id=student.teacher_id if student else None, status="pending",
                      sent_at=submitted - timedelta(hours=6), is_confidential=False)
        db.add(fb)
        db.flush()
        fb.token = sign_value(str(fb.id), "survey")
        fb.created_at = fb.sent_at
        svc.submit_feedback(db, fb, nps, rating, comment, {"channel": "whatsapp"}, submitted_at=submitted)
    # a few pending (sent, not yet answered)
    for i in range(4):
        client = clients[(i * 7) % len(clients)] if clients else None
        if not client or not client_surveys:
            break
        fb = Feedback(survey_id=client_surveys[i % len(client_surveys)].id, trigger="manual", respondent_type="client",
                      client_id=client.id, student_id=client.students[0].id if client.students else None,
                      status="pending", sent_at=datetime.utcnow() - timedelta(days=i + 1))
        db.add(fb)
        db.flush()
        fb.token = sign_value(str(fb.id), "survey")
    # confidential staff eNPS
    staff_survey = surveys.get("staff_enps")
    employees = db.query(Employee).filter(Employee.status.in_(["active", "probation"])).order_by(Employee.id).limit(14).all()
    if staff_survey:
        for i, emp in enumerate(employees):
            submitted = datetime.utcnow() - timedelta(days=rnd.randint(3, 80))
            nps = [9, 8, 10, 6, 7, 9, 4, 8, 9, 7, 10, 5, 8, 9][i % 14]
            fb = Feedback(survey_id=staff_survey.id, trigger="staff_enps", respondent_type="staff", employee_id=emp.id,
                          status="pending", sent_at=submitted - timedelta(hours=3), is_confidential=True)
            db.add(fb)
            db.flush()
            fb.token = sign_value(str(fb.id), "survey")
            svc.submit_feedback(db, fb, nps, None, STAFF_COMMENTS[i % len(STAFF_COMMENTS)], {}, submitted_at=submitted)
    db.flush()


def seed_cases(db: Session, rnd: random.Random) -> None:
    if db.query(func.count(Case.id)).filter(Case.source != "feedback").scalar() >= 25:
        return
    clients = db.query(Client).order_by(Client.id).all()
    staff = (svc.users_with_role(db, "manager") + svc.users_with_role(db, "supervisor")
             + svc.users_with_role(db, "qa_officer") + svc.users_with_role(db, "billing_rep"))
    if not clients:
        return
    for i in range(35):
        case_type, title, description, _cat = CASE_SEEDS[i % len(CASE_SEEDS)]
        client = clients[(i * 3) % len(clients)]
        student = client.students[0] if client.students else None
        phase = i % 5
        age = rnd.randint(0, 6) if phase < 3 else rnd.randint(12, 120)
        created = datetime.utcnow() - timedelta(days=age, hours=rnd.randint(0, 20))
        case = svc.open_case(db, case_type, title, description, client=client, student=student,
                             source=rnd.choice(["portal", "whatsapp", "phone"]), actor=None, created_at=created)
        if phase == 0:
            case.status = "open"
        elif phase == 1:
            case.status = "in_progress"
            db.add(CaseComment(case_id=case.id, user_id=case.assigned_to_id, is_internal=True,
                               text="Called the family, investigating with the teacher today.",
                               created_at=created + timedelta(hours=4)))
        elif phase == 2:
            case.status = "waiting"
            db.add(CaseComment(case_id=case.id, user_id=case.assigned_to_id, is_internal=False,
                               text="We are reviewing the class recording and will come back to you within 24 hours, in sha Allah.",
                               created_at=created + timedelta(hours=6)))
        else:
            case.status = "resolved" if phase == 3 else "closed"
            case.root_cause = rnd.choice(ROOT_CAUSES)
            case.resolution = rnd.choice(RESOLUTIONS)
            case.resolved_at = created + timedelta(hours=rnd.randint(4, 90))
            if case.status == "closed":
                case.closed_at = case.resolved_at + timedelta(hours=6)
            db.add(CaseComment(case_id=case.id, user_id=case.assigned_to_id, is_internal=True,
                               text=f"Root cause: {case.root_cause}.", created_at=case.resolved_at))
            db.add(CaseComment(case_id=case.id, user_id=case.assigned_to_id, is_internal=False,
                               text=case.resolution, created_at=case.resolved_at + timedelta(minutes=10)))
        if case.sla_due_at and case.sla_due_at < datetime.utcnow() and case.status in svc.OPEN_CASE_STATUSES:
            case.sla_breached = True
            if i % 3 == 0:
                case.escalated = True
                case.status = "escalated"
                case.priority = "high" if case.priority in ("low", "medium") else case.priority
                case.escalated_to_id = (staff[0].id if staff else None)
                db.add(CaseComment(case_id=case.id, user_id=None, is_internal=True,
                                   text="SLA breached - auto-escalated to the department head.",
                                   created_at=case.sla_due_at))
        if staff and i % 4 == 0:
            case.assigned_to_id = staff[i % len(staff)].id
    db.flush()


def seed_referrals(db: Session, rnd: random.Random) -> None:
    if db.query(func.count(Referral.id)).scalar() >= 12:
        return
    supervisor = svc.user_by_email(db, "supervisor@oqc.local")
    ambassadors = db.query(Client).filter(Client.is_ambassador.is_(True)).order_by(Client.id).all()
    if not ambassadors:
        ambassadors = db.query(Client).filter(Client.status == "active").order_by(Client.id).limit(4).all()
        for c in ambassadors:
            c.is_ambassador = True
            c.ambassador_invited_at = datetime.utcnow() - timedelta(days=rnd.randint(20, 90))
    referred_pool = db.query(Client).filter(Client.is_ambassador.is_(False), Client.status.in_(["active", "trial"])).order_by(Client.id.desc()).all()
    referred_leads = db.query(Lead).filter(Lead.referral_code.is_(None)).order_by(Lead.id.desc()).limit(6).all()
    statuses = ["invited", "ask", "ask", "lead", "lead", "signed_up", "signed_up", "qualified", "credited", "credited", "expired", "ask"]
    for i, status in enumerate(statuses):
        amb = ambassadors[i % len(ambassadors)]
        svc.ensure_referral_code(amb)
        created = datetime.utcnow() - timedelta(days=rnd.randint(3, 70))
        ref = Referral(ambassador_client_id=amb.id, referral_code=amb.referral_code,
                       referred_name=f"{FIRST[(i * 4) % len(FIRST)]} {LAST[(i * 2) % len(LAST)]}",
                       referred_phone=_phone(rnd, "+44"), status="invited", invited_at=created,
                       owner_id=supervisor.id if supervisor else None,
                       ghl_source_tag=f"ref:{amb.referral_code}", notes="Seeded ambassador ledger entry")
        ref.created_at = created
        db.add(ref)
        db.flush()
        if status in ("lead", "signed_up", "qualified", "credited") and referred_leads:
            lead = referred_leads[i % len(referred_leads)]
            ref.referred_lead_id = lead.id
            ref.referred_name = lead.full_name
            lead.referral_code = amb.referral_code
        if status in ("signed_up", "qualified", "credited") and referred_pool:
            rc = referred_pool[i % len(referred_pool)]
            if rc.id != amb.id:
                ref.referred_client_id = rc.id
                ref.referred_name = rc.full_name
        ref.status = status
        if status == "credited":
            ref.status = "qualified"
            try:
                svc.qualify_referral(db, ref, supervisor,
                                     rationale="Seeded: referred family completed their first paid month (manual qualification).")
            except Exception:
                ref.status = "qualified"
    db.flush()


def seed_freezes(db: Session, rnd: random.Random) -> None:
    """Module 29.11: a few families freeze their subscription; outreach is scheduled 7 days before the freeze ends."""
    if db.query(func.count(RetentionAction.id)).filter(RetentionAction.action_type == "freeze_outreach").scalar():
        return
    supervisors = svc.users_with_role(db, "supervisor") or svc.users_with_role(db, "manager")
    # score the cohort first (without triggering plays) so the families that freeze are genuinely the shakiest ones
    scored = []
    for st in db.query(Student).filter(Student.status == "active").order_by(Student.id).all():
        res = retention_svc.compute_risk(db, st, actor=None, act=False)
        scored.append((res["score"], st.id, st))
    scored.sort(key=lambda x: (-x[0], x[1]))
    candidates = [st for _score, _sid, st in scored[:4]]
    for i, st in enumerate(candidates):
        st.status = "frozen"
        freeze_end = date.today() + timedelta(days=[4, 9, 16, 25][i % 4])
        db.add(RetentionAction(student_id=st.id, client_id=st.client_id, action_type="freeze_outreach", trigger="freeze",
                               risk_score_at_trigger=st.risk_score, status="scheduled",
                               scheduled_at=datetime.combine(freeze_end - timedelta(days=7), datetime.min.time().replace(hour=10)),
                               owner_id=supervisors[i % len(supervisors)].id if supervisors else None,
                               notes=f"Freeze ends {freeze_end.isoformat()} - confirm reactivation and re-book the slot."))
    db.flush()


def seed_retention(db: Session, rnd: random.Random) -> None:
    students = db.query(Student).filter(Student.status.in_(["active", "trial", "frozen"])).order_by(Student.id).all()
    if db.query(func.count(RetentionAction.id)).scalar() < 10:
        supervisors = svc.users_with_role(db, "supervisor") + svc.users_with_role(db, "manager")
        risky = sorted(students, key=lambda s: -(s.risk_score or 0))[:16]
        types = ["cohort_call", "pre_leave_offer", "call", "teacher_change", "discount_offer", "win_back_sequence"]
        outcomes = ["Family reassured, schedule adjusted", "Teacher changed, attendance recovered",
                    "Offer declined, family left", "Free week accepted, subscription continued", "No answer after three attempts"]
        for i, st in enumerate(risky):
            status = ["scheduled", "in_progress", "completed", "succeeded", "succeeded", "failed"][i % 6]
            scheduled = datetime.utcnow() - timedelta(days=rnd.randint(-6, 40))
            db.add(RetentionAction(student_id=st.id, client_id=st.client_id, action_type=types[i % len(types)],
                                   trigger="risk_score" if i % 3 else "manual", risk_score_at_trigger=st.risk_score,
                                   status=status, scheduled_at=scheduled,
                                   completed_at=(scheduled + timedelta(days=1)) if status in ("completed", "succeeded", "failed") else None,
                                   owner_id=supervisors[i % len(supervisors)].id if supervisors else None,
                                   outcome=rnd.choice(outcomes) if status in ("completed", "succeeded", "failed") else None,
                                   notes="Seeded retention play."))
        db.flush()
    # score every student with the churn model (creates win-back plays and alerts for new high risk)
    if not svc.setting(db, "crm_seed_risk_scored"):
        for st in students:
            retention_svc.compute_risk(db, st, actor=None)
        _setting(db, "crm_seed_risk_scored", {"value": True}, "retention", "Seed marker: churn risk scored for the demo cohort")
    db.flush()


# ----------------------------------------------------------------------------- entry point
def run(db: Session) -> None:
    rnd = random.Random(20260909)
    _setting(db, "whatsapp_verify_token", {"value": "oqc-verify"}, "integrations", "Meta webhook verification token")
    _setting(db, "lead_contact_sla_minutes", {"value": 60}, "leads", "Target first-contact time for a new lead")
    _setting(db, "retention_cohort_call_day", {"value": 5}, "retention", "Day of the month for COO cohort calls")
    sources = seed_sources(db)
    seed_templates(db)
    seed_sequences(db)
    campaigns = seed_campaigns(db, rnd)
    leads = seed_leads(db, rnd, sources, campaigns)
    seed_conversations(db, rnd, leads)
    seed_enrollments(db, rnd, leads)
    seed_trials(db, rnd, leads)
    seed_surveys_feedback(db, rnd)
    seed_cases(db, rnd)
    seed_referrals(db, rnd)
    seed_freezes(db, rnd)
    seed_retention(db, rnd)
    db.flush()
