"""CRM automation seed: the tag taxonomy and the workflow catalogue from the customers' GoHighLevel plan.

Idempotent: a tag or workflow that already exists is left exactly as staff last saved it; only missing ones
are created. Demo tags are applied to existing leads and families from their own data (country, source,
status). A few workflow runs are started for the newest leads so the pages show real history.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.automation import Tag, Workflow, WorkflowRun
from app.models.crm import Lead
from app.models.people import Client, Student
from app.services import automation as auto

TAGS = [
    # source
    ("src:meta", "source", "sky", "Came from a Meta (Facebook / Instagram) lead form or ad"),
    ("src:google", "source", "sky", "Came from Google Ads or search"),
    ("src:website", "source", "sky", "Registered through the website form"),
    ("src:whatsapp", "source", "sky", "Wrote to us on WhatsApp first"),
    ("src:referral", "source", "sky", "Referred by an existing family"),
    # market
    ("market:us", "market", "indigo", "United States (SMS needs 10DLC approval)"),
    ("market:uk", "market", "indigo", "United Kingdom (GDPR)"),
    ("market:canada", "market", "indigo", "Canada (CASL express consent)"),
    ("market:australia", "market", "indigo", "Australia"),
    ("market:eu", "market", "indigo", "European Union (GDPR)"),
    ("market:other", "market", "indigo", "Any other country"),
    # status
    ("status:new-lead", "status", "amber", "A lead that has not been contacted yet"),
    ("status:demo-booked", "status", "amber", "A demo / trial class is booked"),
    ("status:demo-completed", "status", "amber", "The demo was attended"),
    ("status:admission-review", "status", "amber", "Admission team is reviewing"),
    ("status:payment-pending", "status", "amber", "Payment link sent, waiting"),
    ("status:enrolled", "status", "emerald", "An active student"),
    ("status:on-leave", "status", "violet", "On approved leave"),
    ("status:frozen", "status", "violet", "Subscription frozen"),
    ("status:cancelled", "status", "rose", "Subscription cancelled"),
    ("status:alumni", "status", "teal", "Completed a course"),
    ("status:stale", "status", "slate", "No response; parked"),
    # temperature
    ("temp:hot", "temperature", "rose", "Ready to enrol"),
    ("temp:warm", "temperature", "orange", "Interested, needs follow-up"),
    ("temp:cold", "temperature", "slate", "No recent engagement"),
    # consent
    ("consent:email", "consent", "emerald", "May be emailed (GDPR / CASL consent recorded)"),
    ("consent:sms", "consent", "emerald", "May be sent SMS"),
    ("consent:whatsapp", "consent", "emerald", "May be sent WhatsApp messages"),
    ("unsubscribed", "consent", "rose", "Asked not to be contacted by email; every email step skips this contact"),
    # other
    ("objection:price", "other", "orange", "Raised a price objection (starts the price-objection handler)"),
    ("scholarship", "other", "teal", "Asked about a scholarship"),
    ("interested-next-course", "other", "teal", "Alumni interested in the next course"),
]

MARKET_BY_COUNTRY = {"United States": "market:us", "United Kingdom": "market:uk", "Canada": "market:canada", "Australia": "market:australia",
                     "Ireland": "market:eu", "Germany": "market:eu", "France": "market:eu", "Netherlands": "market:eu"}
SOURCE_TAG = {"Meta Ads": "src:meta", "Google Ads": "src:google", "Website": "src:website", "WhatsApp": "src:whatsapp", "Referral": "src:referral"}

ACQ = "Student Acquisition"
LEAVE = "Leave & Freeze"
ENGAGE = "Active, Cancelled & Alumni"

WORKFLOWS = [
    # ------------------------------------------------------------------ Group A: the numbered automations
    dict(code="AUTO-003", name="New Lead Notification", category="acquisition", pipeline=ACQ, trigger="lead.created",
         description="The moment a lead arrives from any source, the assigned rep gets an alert with the details, the lead is tagged by source and market, and the first WhatsApp goes out.",
         steps=[
             {"kind": "add_tag", "tag": "status:new-lead"},
             {"kind": "notify_staff", "to": "assigned", "title": "New lead: {{name}}", "body": "A new lead for {{student}} ({{source}}) is waiting for a first call."},
             {"kind": "send_whatsapp", "template": "lead_first_touch"},
             {"kind": "wait", "hours": 48},
             {"kind": "condition", "field": "stage", "op": "in", "value": "new,contacted", "then": "continue", "else": "exit"},
             {"kind": "notify_staff", "to": "assigned", "title": "Lead not booked after 48 hours: {{name}}", "body": "No demo booked yet. Call the family today."},
         ], exit_on={"lead_stages": ["won", "lost"]}),
    dict(code="AUTO-002", name="Lead Magnet Delivery", category="acquisition", pipeline=ACQ, trigger="tag.added", trigger_filter={"tag": "src:website"},
         description="Website sign-ups get the welcome email with the free-class guide, are tagged, and land in the pipeline's first stage.",
         steps=[
             {"kind": "send_email", "subject": "Your free Quran class guide", "body": "Assalamu Alaikum {{name}}, thank you for your interest in Online Quran College for {{student}}. Your guide is attached in the portal: {{link}}. Reply to this email to book the free first class."},
             {"kind": "add_tag", "tag": "temp:warm"},
         ]),
    dict(code="AUTO-005", name="Discovery Call / Demo Confirmation", category="acquisition", pipeline=ACQ, trigger="trial.scheduled",
         description="When a demo is booked the family gets a confirmation with the date, time and join link, and the lead is tagged demo-booked.",
         steps=[
             {"kind": "add_tag", "tag": "status:demo-booked"},
             {"kind": "remove_tag", "tag": "status:new-lead"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, {{student}}'s free demo class is confirmed for {{date}}. Join link: {{link}}. See you there, in sha Allah."},
             {"kind": "send_email", "subject": "Demo class confirmed for {{student}}", "body": "Assalamu Alaikum {{name}}, {{student}}'s demo class is confirmed for {{date}}. Join from {{link}}. Please have a quiet room and a working microphone ready."},
         ], run_once_per_contact=False),
    dict(code="AUTO-006", name="Pre-Demo Reminders (24h and 1h)", category="acquisition", pipeline=ACQ, trigger="trial.scheduled",
         description="Two reminders before the demo, 24 hours and 1 hour ahead, by WhatsApp and email. Reminders cut no-shows by a third.",
         steps=[
             {"kind": "wait_until", "field": "trial_at", "hours_before": 24},
             {"kind": "send_whatsapp", "body": "Reminder: {{student}}'s demo class is tomorrow at the booked time. Join link: {{link}}."},
             {"kind": "wait_until", "field": "trial_at", "hours_before": 1},
             {"kind": "send_whatsapp", "body": "{{student}}'s demo class starts in one hour. Join here: {{link}}."},
         ], run_once_per_contact=False, needs="The trial's date is read from the booking; reminders are skipped if the booking has no time."),
    dict(code="AUTO-007", name="Post-Demo Nurture (5 touches / 7 days)", category="acquisition", pipeline=ACQ, trigger="trial.attended",
         description="After an attended demo: thank you, social proof, course overview, FAQ, then the final offer, spread over a week. Stops the moment the family replies or enrols.",
         steps=[
             {"kind": "add_tag", "tag": "status:demo-completed"},
             {"kind": "remove_tag", "tag": "status:demo-booked"},
             {"kind": "send_whatsapp", "template": "trial_follow_up"},
             {"kind": "wait", "days": 1},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, families in {{college}} tell us their children look forward to class every day. {{student}}'s teacher was delighted with the demo."},
             {"kind": "wait", "days": 2},
             {"kind": "send_email", "subject": "How {{student}}'s course would run", "body": "Assalamu Alaikum {{name}}, here is how the course works: a fixed teacher, a fixed time, a monthly progress card and a parent portal at {{link}}."},
             {"kind": "wait", "days": 2},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, the questions families ask most: fees are monthly, you may pause any time, and you may change the time or teacher. Anything else I can answer?"},
             {"kind": "wait", "days": 2},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, this is my last message about {{student}}'s place. We can still hold the same slot until tomorrow, in sha Allah."},
             {"kind": "move_stage", "stage": "negotiation", "reason": "Nurture sequence finished"},
         ], exit_on={"lead_stages": ["won", "lost"], "reply": True}),
    dict(code="AUTO-008", name="Price Objection Handler", category="acquisition", pipeline=ACQ, trigger="tag.added", trigger_filter={"tag": "objection:price"},
         description="When a rep tags a lead with a price objection, three emails over five days explain value, the pause option and the scholarship route.",
         steps=[
             {"kind": "send_email", "subject": "About the fee for {{student}}'s classes", "body": "Assalamu Alaikum {{name}}, the monthly fee covers a dedicated teacher, a fixed daily slot and monthly progress cards. You pay month by month and can pause whenever you need."},
             {"kind": "wait", "days": 2},
             {"kind": "send_email", "subject": "A smaller plan for {{student}}", "body": "Assalamu Alaikum {{name}}, fewer days per week brings the fee down. Tell me the days that suit and I will send the exact amount."},
             {"kind": "wait", "days": 3},
             {"kind": "send_email", "subject": "Scholarship support", "body": "Assalamu Alaikum {{name}}, families who need it can apply for a part scholarship. Reply SCHOLARSHIP and I will send the short form."},
             {"kind": "notify_staff", "to": "assigned", "title": "Price objection emails finished: {{name}}", "body": "Call the family to close or offer the scholarship form."},
         ], exit_on={"lead_stages": ["won", "lost"], "reply": True}),
    dict(code="AUTO-009", name="Scholarship Inquiry Handler", category="acquisition", pipeline=ACQ, trigger="tag.added", trigger_filter={"tag": "scholarship"},
         description="A scholarship enquiry gets an immediate acknowledgement, and the admissions head gets a task to review it.",
         steps=[
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, we have received your scholarship enquiry for {{student}}. Our admissions team will reply within two working days, in sha Allah."},
             {"kind": "create_task", "to": "hod:academics", "title": "Review scholarship enquiry", "due_days": 2},
         ]),
    dict(code="AUTO-010", name="Lost Lead Reactivation (30 days)", category="acquisition", pipeline=ACQ, trigger="lead.lost",
         description="Thirty days after a lead is lost, one gentle 'what changed?' message. If the family replies the lead is reopened for the rep.",
         steps=[
             {"kind": "add_tag", "tag": "status:stale"},
             {"kind": "wait", "days": 30},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, it has been a month since we spoke about {{student}}'s Quran classes. Has anything changed? We would still love to help."},
         ], exit_on={"lead_stages": ["won"], "reply": True}),
    dict(code="AUTO-011", name="Enrollment Welcome (7 messages / 14 days)", category="onboarding", pipeline=ACQ, trigger="lead.converted",
         description="From enrolment: welcome, portal access, first-class preparation, meet the teacher, how progress is reported, the community, and a two-week check-in.",
         steps=[
             {"kind": "add_tag", "tag": "status:enrolled"},
             {"kind": "remove_tag", "tag": "status:payment-pending"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, welcome to {{college}}! {{student}}'s classes are set up. Your family portal is at {{link}}."},
             {"kind": "send_email", "subject": "Welcome to Online Quran College", "body": "Assalamu Alaikum {{name}}, welcome. Sign in to the family portal at {{link}} with the temporary password we sent to see the schedule, attendance and invoices."},
             {"kind": "wait", "days": 1},
             {"kind": "send_whatsapp", "body": "Before {{student}}'s first class: a quiet room, a charged device, the Quran at hand, and the join link from the portal. See you there, in sha Allah."},
             {"kind": "wait", "days": 2},
             {"kind": "send_whatsapp", "body": "{{student}}'s teacher is {{teacher}}. You can message the college from the portal any time with questions about the lessons."},
             {"kind": "wait", "days": 4},
             {"kind": "send_email", "subject": "How we report {{student}}'s progress", "body": "Every month you receive a result card in the portal, and the teacher writes a short note after each evaluation."},
             {"kind": "wait", "days": 4},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, families often share tips in our parents' community. Ask your rep for the invite link."},
             {"kind": "wait", "days": 3},
             {"kind": "send_whatsapp", "body": "Two weeks in! How are {{student}}'s classes going? Reply with anything we can improve."},
         ], exit_on={"client_statuses": ["churned", "drop_out", "black_list"]}),
    dict(code="AUTO-012", name="Portal Credential Delivery on First Payment", category="onboarding", pipeline=ACQ, trigger="payment.first",
         description="The first confirmed payment sends the family their portal sign-in instructions and tags the family enrolled.",
         steps=[
             {"kind": "add_tag", "tag": "status:enrolled"},
             {"kind": "send_email", "subject": "Your Online Quran College portal", "body": "Assalamu Alaikum {{name}}, we received {{amount}}. Your family portal is ready at {{link}}. Sign in with your email and the temporary password; you will be asked to choose a new one."},
             {"kind": "send_whatsapp", "body": "Payment of {{amount}} received, JazakAllah Khair. Your portal sign-in details are in your email."},
         ]),
    dict(code="AUTO-013", name="First Class Reminder", category="onboarding", pipeline=ACQ, trigger="lead.converted",
         description="The day before the first class: time, link and what to prepare.",
         steps=[
             {"kind": "wait", "hours": 20},
             {"kind": "send_whatsapp", "body": "Reminder: {{student}}'s first class is tomorrow. The time and join link are in the portal: {{link}}."},
         ]),
    dict(code="AUTO-014", name="No-Show Recovery", category="engagement", pipeline=ENGAGE, trigger="class.student_absent",
         description="Right after a missed class the family gets a message with the option to reschedule, and the teacher is told. Three absences in a month alert the coordinator.",
         steps=[
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, we missed {{student}} in today's class. Everything is fine, in sha Allah? Reply to pick another time if needed."},
             {"kind": "notify_staff", "to": "teacher", "title": "Student absent: {{student}}", "body": "The family has been messaged. Note anything the coordinator should know."},
         ], run_once_per_contact=False),
    dict(code="AUTO-015", name="At-Risk Student Alert (14-day inactivity)", category="retention", pipeline=ENGAGE, trigger="student.inactive",
         description="A student with no attended class for fourteen days: the coordinator gets a task, the family a caring message, and the win-back sequence starts.",
         steps=[
             {"kind": "add_tag", "tag": "temp:cold"},
             {"kind": "create_task", "to": "role:supervisor", "title": "Call family: 14 days without a class", "due_days": 1},
             {"kind": "send_whatsapp", "template": "win_back"},
             {"kind": "enroll_sequence", "sequence_type": "win_back"},
         ], run_once_per_contact=False),
    dict(code="AUTO-016", name="Monthly Progress Check-In", category="engagement", pipeline=ENGAGE, trigger="lead.converted",
         description="Every month after enrolment, a personal check-in and a one-question survey. Ends when the family leaves.",
         steps=[
             {"kind": "wait", "days": 30},
             {"kind": "send_whatsapp", "template": "feedback_survey"},
             {"kind": "wait", "days": 30},
             {"kind": "send_whatsapp", "template": "feedback_survey"},
             {"kind": "wait", "days": 30},
             {"kind": "send_whatsapp", "template": "feedback_survey"},
         ], exit_on={"client_statuses": ["churned", "drop_out", "black_list", "inactive"]}),
    dict(code="AUTO-017", name="Course Completion Celebration + Next Course", category="alumni", pipeline=ENGAGE, trigger="course.completed",
         description="On completion: congratulations, the certificate link, the alumni tag, and after three days the next-course offer.",
         steps=[
             {"kind": "add_tag", "tag": "status:alumni"},
             {"kind": "send_whatsapp", "body": "Congratulations {{name}}! {{student}} has completed {{course}}. The certificate is here: {{link}}. May Allah accept it."},
             {"kind": "send_email", "subject": "{{student}} completed {{course}}", "body": "Assalamu Alaikum {{name}}, congratulations. {{student}}'s certificate: {{link}}. The next level is ready whenever you are."},
             {"kind": "wait", "days": 3},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, {{student}} is ready for the next level. Shall we continue with the same teacher and time? Reply YES and we will set it up."},
             {"kind": "notify_staff", "to": "assigned", "title": "Upsell: {{student}} completed {{course}}", "body": "Follow up on the next-course offer."},
         ], run_once_per_contact=False),
    dict(code="AUTO-018", name="Review Request (7 days after completion)", category="alumni", pipeline=ENGAGE, trigger="course.completed",
         description="A week after completion, a 60-second review request with the direct link.",
         steps=[
             {"kind": "wait", "days": 7},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, if {{student}}'s journey with us was a good one, a 60-second review helps other families find us: {{review_link}}. JazakAllah Khair."},
         ], run_once_per_contact=False, needs="Set the review link in Configuration › Setup (Branch Properties › review_link) once the Google Business profile is connected."),
    dict(code="AUTO-019", name="Referral Programme Trigger (NPS 9-10)", category="alumni", pipeline=ENGAGE, trigger="feedback.submitted", trigger_filter={"nps_min": 9},
         description="A promoter score of 9 or 10 invites the family to the ambassador programme with their referral link.",
         steps=[
             {"kind": "add_tag", "tag": "temp:hot"},
             {"kind": "send_whatsapp", "template": "ambassador_invite"},
         ]),
    dict(code="AUTO-020", name="Database Reactivation (90-day cold leads)", category="acquisition", pipeline=ACQ, trigger="lead.stale", trigger_filter={},
         description="Leads that have gone quiet get a 'here is what is new' message and are tagged cold; replies reopen the lead.",
         steps=[
             {"kind": "add_tag", "tag": "temp:cold"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, we have added new courses and evening slots since we last spoke. Would {{student}} like a free class to try them?"},
         ]),
    # ------------------------------------------------------------------ Pipeline 1: Student Acquisition, stage by stage (the WorkFlows sheet)
    dict(code="P1-S2", name="Pipeline 1 · Demo Booking Pending (3-touch reminder)", category="acquisition", pipeline=ACQ, stage="Demo Booking Pending",
         trigger="lead.stage_changed", trigger_filter={"stage": "contacted"},
         description="No demo booked after the first contact: reminder, wait a day, reminder, wait 12 hours, final reminder, then mark stale.",
         steps=[
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, shall we book {{student}}'s free demo class this week? Reply with a day and time that suits."},
             {"kind": "wait", "hours": 24},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "contacted", "then": "continue", "else": "exit"},
             {"kind": "send_email", "subject": "{{student}}'s free demo class", "body": "Assalamu Alaikum {{name}}, we still have demo slots open this week for {{student}}. Reply with a time and we will confirm."},
             {"kind": "wait", "hours": 12},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, last reminder about {{student}}'s free demo. Shall I keep a slot for you?"},
             {"kind": "wait", "hours": 24},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "contacted", "then": "continue", "else": "exit"},
             {"kind": "notify_staff", "to": "assigned", "title": "No demo booked: {{name}}", "body": "Three reminders went out. Call, or mark the lead lost."},
         ], exit_on={"lead_stages": ["trial_scheduled", "trial_done", "negotiation", "payment_pending", "won", "lost"], "reply": True}),
    dict(code="P1-S5", name="Pipeline 1 · Admission Review", category="acquisition", pipeline=ACQ, stage="Admission Review",
         trigger="lead.stage_changed", trigger_filter={"stage": "negotiation"},
         description="The family is sent the admission form and the admissions head is told; after a day a reminder, after two the rep is asked to decide.",
         steps=[
             {"kind": "add_tag", "tag": "status:admission-review"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, to enrol {{student}} please complete the short admission form in the portal: {{link}}."},
             {"kind": "notify_staff", "to": "hod:academics", "title": "Admission review: {{name}}", "body": "Review the family's details and approve or decline."},
             {"kind": "wait", "hours": 24},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "negotiation", "then": "continue", "else": "exit"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, a reminder to complete {{student}}'s admission form so we can reserve the teacher: {{link}}."},
             {"kind": "wait", "hours": 24},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "negotiation", "then": "continue", "else": "exit"},
             {"kind": "notify_staff", "to": "assigned", "title": "Admission decision due: {{name}}", "body": "Move the lead to Payment Pending or mark it lost."},
         ], exit_on={"lead_stages": ["payment_pending", "won", "lost"]}),
    dict(code="P1-S6", name="Pipeline 1 · Payment Pending", category="acquisition", pipeline=ACQ, stage="Payment Pending",
         trigger="lead.stage_changed", trigger_filter={"stage": "payment_pending"},
         description="The payment link goes out, then reminders at 12 hours, 24 hours and 3 days. Billing is told at each step.",
         steps=[
             {"kind": "add_tag", "tag": "status:payment-pending"},
             {"kind": "remove_tag", "tag": "status:admission-review"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, {{student}}'s place is approved. Pay the first month securely here to start: {{link}}."},
             {"kind": "notify_staff", "to": "role:billing_rep", "title": "Payment link sent: {{name}}", "body": "Watch for the receipt and confirm it."},
             {"kind": "wait", "hours": 12},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "payment_pending", "then": "continue", "else": "exit"},
             {"kind": "send_whatsapp", "body": "Reminder: {{student}}'s place is held for 48 hours. Payment link: {{link}}."},
             {"kind": "wait", "hours": 24},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "payment_pending", "then": "continue", "else": "exit"},
             {"kind": "send_email", "subject": "Reserve {{student}}'s place", "body": "Assalamu Alaikum {{name}}, the teacher and slot are reserved for {{student}}. Complete the payment here: {{link}}."},
             {"kind": "wait", "days": 3},
             {"kind": "condition", "field": "stage", "op": "eq", "value": "payment_pending", "then": "continue", "else": "exit"},
             {"kind": "notify_staff", "to": "assigned", "title": "No payment after 4 days: {{name}}", "body": "Call the family, or mark the lead lost to release the slot."},
         ], exit_on={"lead_stages": ["won", "lost"]}),
    dict(code="P1-S8", name="Pipeline 1 · Stale / Lost cleanup", category="acquisition", pipeline=ACQ, stage="Stale / Lost", trigger="lead.lost",
         description="A lost lead loses its working tags and gets the stale tag, so smart lists stay clean.",
         steps=[
             {"kind": "remove_tag", "tag": "status:new-lead"}, {"kind": "remove_tag", "tag": "status:demo-booked"},
             {"kind": "remove_tag", "tag": "status:demo-completed"}, {"kind": "remove_tag", "tag": "status:admission-review"},
             {"kind": "remove_tag", "tag": "status:payment-pending"}, {"kind": "add_tag", "tag": "status:stale"},
         ]),
    # ------------------------------------------------------------------ Pipeline 2: Leave & Freeze
    dict(code="P2-FREEZE", name="Pipeline 2 · Freeze Active + Re-engagement", category="retention", pipeline=LEAVE, stage="Freeze Active",
         trigger="subscription.frozen",
         description="While a subscription is frozen: a confirmation, a caring message after ten days, and the day before the freeze ends an offer to reserve the same slot.",
         steps=[
             {"kind": "add_tag", "tag": "status:frozen"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, {{student}}'s classes are paused as requested. The slot is kept for you until the freeze ends, in sha Allah."},
             {"kind": "wait", "days": 10},
             {"kind": "condition", "field": "tags.status:frozen", "op": "truthy", "value": "", "then": "continue", "else": "exit"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, we hope all is well with your family. {{student}}'s teacher sends salaam and looks forward to the return."},
             {"kind": "wait_until", "field": "freeze_end", "hours_before": 24},
             {"kind": "send_whatsapp", "template": "freeze_reactivation"},
         ], run_once_per_contact=False),
    dict(code="P2-RESUME", name="Pipeline 2 · Student Resumed", category="retention", pipeline=LEAVE, stage="Student Resumed", trigger="subscription.resumed",
         description="Welcome back by WhatsApp and email, the frozen tag removed, and the teacher asked to confirm attendance at the first class back.",
         steps=[
             {"kind": "remove_tag", "tag": "status:frozen"}, {"kind": "add_tag", "tag": "status:enrolled"},
             {"kind": "send_whatsapp", "body": "Welcome back {{name}}! {{student}}'s classes resume from today with {{teacher}}. The schedule is in the portal: {{link}}."},
             {"kind": "send_email", "subject": "Welcome back, {{student}}", "body": "Assalamu Alaikum {{name}}, {{student}}'s classes have resumed. Check the schedule at {{link}}."},
             {"kind": "create_task", "to": "teacher", "title": "Confirm the student attended the first class back", "due_days": 2},
         ], run_once_per_contact=False),
    # ------------------------------------------------------------------ Pipeline 3: Active, Cancelled, Alumni
    dict(code="P3-CANCEL", name="Pipeline 3 · Cancelled Students Engagement", category="retention", pipeline=ENGAGE, stage="Cancelled",
         trigger="subscription.cancelled",
         description="After a cancellation: tags, a feedback request, a win-back task for the rep, and a 'door is open' message after a month.",
         steps=[
             {"kind": "add_tag", "tag": "status:cancelled"}, {"kind": "remove_tag", "tag": "status:enrolled"},
             {"kind": "send_whatsapp", "template": "feedback_survey"},
             {"kind": "create_task", "to": "assigned", "title": "Win-back call after cancellation", "due_days": 3},
             {"kind": "wait", "days": 30},
             {"kind": "condition", "field": "active_subscription", "op": "truthy", "value": "", "then": "exit", "else": "continue"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, the door is always open for {{student}}. We can restart with the same teacher whenever you are ready."},
         ], run_once_per_contact=False),
    dict(code="P3-ALUMNI", name="Pipeline 3 · Alumni Win-back (15 / 15 / 30 days)", category="alumni", pipeline=ENGAGE, stage="Alumni & Upsell",
         trigger="course.completed",
         description="Alumni hear from us at 15, 30 and 60 days with the next course. Stops when the family enrols again.",
         steps=[
             {"kind": "wait", "days": 15},
             {"kind": "condition", "field": "active_subscription", "op": "truthy", "value": "", "then": "exit", "else": "continue"},
             {"kind": "send_whatsapp", "body": "Assalamu Alaikum {{name}}, {{student}}'s next course is open for enrolment. Same teacher, same time, if you wish."},
             {"kind": "wait", "days": 15},
             {"kind": "condition", "field": "active_subscription", "op": "truthy", "value": "", "then": "exit", "else": "continue"},
             {"kind": "send_email", "subject": "What alumni families chose next", "body": "Assalamu Alaikum {{name}}, most families continue to Tajweed or Hifz after {{course}}. Reply and we will suggest the right path for {{student}}."},
             {"kind": "wait", "days": 30},
             {"kind": "condition", "field": "active_subscription", "op": "truthy", "value": "", "then": "exit", "else": "continue"},
             {"kind": "notify_staff", "to": "assigned", "title": "Alumni not re-enrolled: {{student}}", "body": "Two months since completion. One personal call."},
         ], run_once_per_contact=False),
    # ------------------------------------------------------------------ compliance
    dict(code="COMP-CONSENT", name="Compliance · Consent tags on enrolment", category="compliance", pipeline=None, trigger="lead.converted",
         description="Enrolment records the channels the family agreed to. Email and SMS steps check these tags before sending.",
         steps=[
             {"kind": "condition", "field": "consent_given", "op": "truthy", "value": "", "then": "continue", "else": "exit"},
             {"kind": "add_tag", "tag": "consent:email"}, {"kind": "add_tag", "tag": "consent:whatsapp"},
         ]),
]


def seed_tags(db: Session) -> dict[str, Tag]:
    out = {}
    for name, cat, color, desc in TAGS:
        t = db.query(Tag).filter(Tag.name == name).first()
        if not t:
            t = Tag(name=name, category=cat, color=color, description=desc)
            db.add(t)
        out[name] = t
    db.flush()
    return out


def seed_workflows(db: Session) -> int:
    created = 0
    for i, spec in enumerate(WORKFLOWS):
        existing = db.query(Workflow).filter(Workflow.code == spec["code"]).first()
        if existing:
            if spec["code"] == "AUTO-018":
                # The review request now carries the configurable {{review_link}}; refresh a row seeded with {{link}}.
                steps = list(existing.steps or [])
                if any("{{link}}" in str(st.get("body", "")) for st in steps if isinstance(st, dict)):
                    existing.steps = [dict(st, body=st["body"].replace("{{link}}", "{{review_link}}")) if isinstance(st, dict) and "body" in st else st
                                      for st in steps]
                    existing.needs = spec.get("needs")
            continue
        errors = auto.validate_steps(spec["steps"])
        assert not errors, f"{spec['code']}: {errors}"
        db.add(Workflow(code=spec["code"], name=spec["name"], description=spec.get("description"), category=spec.get("category", "acquisition"),
                        pipeline=spec.get("pipeline"), stage=spec.get("stage"), trigger=spec["trigger"], trigger_filter=spec.get("trigger_filter") or {},
                        steps=spec["steps"], exit_on=spec.get("exit_on") or {}, run_once_per_contact=spec.get("run_once_per_contact", True),
                        is_active=True, needs=spec.get("needs"), sort_no=i))
        created += 1
    db.flush()
    return created


def tag_contacts(db: Session) -> int:
    """Market, source and status tags from the records' own data. Silent: no tag.added events during seeding."""
    n = 0
    for lead in db.query(Lead).all():
        n += auto.add_tag(db, "lead", lead.id, MARKET_BY_COUNTRY.get(lead.country or "", "market:other"), "seed", emit_event=False)
        if lead.source and SOURCE_TAG.get(lead.source.name):
            n += auto.add_tag(db, "lead", lead.id, SOURCE_TAG[lead.source.name], "seed", emit_event=False)
        status_tag = {"new": "status:new-lead", "trial_scheduled": "status:demo-booked", "trial_done": "status:demo-completed",
                      "negotiation": "status:admission-review", "payment_pending": "status:payment-pending", "won": "status:enrolled",
                      "lost": "status:stale"}.get(lead.stage)
        if status_tag:
            n += auto.add_tag(db, "lead", lead.id, status_tag, "seed", emit_event=False)
        if lead.lost_reason and "price" in lead.lost_reason.lower():
            n += auto.add_tag(db, "lead", lead.id, "objection:price", "seed", emit_event=False)
    for c in db.query(Client).all():
        n += auto.add_tag(db, "client", c.id, MARKET_BY_COUNTRY.get(c.country or "", "market:other"), "seed", emit_event=False)
        st = {"active": "status:enrolled", "regular": "status:enrolled", "frozen": "status:frozen", "on_leave": "status:on-leave",
              "churned": "status:cancelled", "drop_out": "status:cancelled", "pass_out": "status:alumni"}.get(c.status)
        if st:
            n += auto.add_tag(db, "client", c.id, st, "seed", emit_event=False)
        if c.consent_given:
            n += auto.add_tag(db, "client", c.id, "consent:email", "seed", emit_event=False)
        if c.whatsapp_opt_in:
            n += auto.add_tag(db, "client", c.id, "consent:whatsapp", "seed", emit_event=False)
    for s in db.query(Student).filter(Student.status.in_(["graduated", "pass_out"])).all():
        n += auto.add_tag(db, "student", s.id, "status:alumni", "seed", emit_event=False)
    return n


def demo_runs(db: Session) -> int:
    """A little history for the pages: the three newest open leads go through New Lead Notification."""
    if db.query(WorkflowRun).count():
        return 0
    base = datetime.utcnow() - timedelta(days=3)
    started = 0
    for i, lead in enumerate(db.query(Lead).filter(Lead.stage.in_(["new", "contacted"])).order_by(Lead.id.desc()).limit(3)):
        started += len(auto.emit(db, "lead.created", "lead", lead.id, {"source": lead.source.name if lead.source else "", "stage": lead.stage}, now=base + timedelta(hours=i)))
    auto.advance_due_runs(db, now=datetime.utcnow())
    return started


def run(db: Session) -> None:
    seed_tags(db)
    created = seed_workflows(db)
    tagged = tag_contacts(db)
    runs = demo_runs(db)
    db.flush()
    print(f"    automation: {created} workflow(s) created, {tagged} tag(s) applied, {runs} demo run(s)")
