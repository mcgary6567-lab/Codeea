# Privacy & Compliance

This system records people who have not chosen to be recorded (customers) and
generates biometric data about people whose livelihood depends on cooperating
(staff). Both facts carry legal and ethical obligations that are cheaper to
build in than to retrofit.

---

## Customers

| Obligation | Implementation |
|---|---|
| Notice | Signage at every entrance, in the local language, stating CCTV and automated analytics are in use, with the operator's contact |
| Minimisation | Only counts and anonymous attributes leave the edge. No customer identification, no re-identification across visits, no demographic inference |
| Face blurring | Applied **at the edge before any frame is persisted or uploaded**. An unblurred customer face must never exist in cloud storage |
| No customer biometrics | Do not enrol, match, or store customer face embeddings. If a client asks for repeat-customer recognition, that is a separate product with a separate legal review |
| Retention | Snapshots 30 days default, 90 maximum, enforced by S3 lifecycle rule |
| Access | Raw snapshots reachable only by `owner` and `auditor`; every access audit-logged |

---

## Staff

Biometric data about employees is special-category data almost everywhere
(GDPR Art. 9, India DPDP Act, Illinois BIPA, and others). The recurring legal
problem is that consent from an employee is rarely considered freely given —
if refusing costs you your shift, it is not consent.

**Therefore:**

1. **NFC or PIN attendance is always available** and is the default. Face
   recognition is opt-in and additive.
2. **Consent is a record, not a checkbox.** `biometric_consents` stores who
   consented, when, by what method, and a copy of the signed form. Revocation is
   a first-class operation with a timestamp.
3. **No enrolment without an active consent record.** `POST /staff/{id}/face-enrol`
   returns `409` otherwise. This is the technical control that backs the policy —
   write a test for it.
4. **Embeddings, never images.** Store the 512-d vector, KMS-encrypted. Discard
   the enrolment photo immediately after extraction.
5. **Purge on exit or revocation** within 30 days, by a nightly job keyed on
   `face_embeddings.purge_after`. Test this job.
6. **No surveillance beyond the stated purpose.** Attendance and safety only.
   Do not build productivity scoring, idle-time tracking, or bathroom-break
   analytics on this data. If asked, decline and explain — it converts a
   compliance-clean system into a legally hostile one and destroys staff trust.

---

## Variance alerts and employment consequences

The cup-variance signal can lead to disciplinary action. Build accordingly:

- Every alert carries a **confidence band** and the **evidence** (snapshots, POS
  orders, detection events) for the window.
- Alert copy states a discrepancy was detected. It does not name a cause and
  does not name a person.
- The audit log records who viewed the evidence and what action was recorded.
- Documentation delivered to the client must state plainly that vision counts
  are estimates and should not be the sole basis for a disciplinary decision.

This is not legal caution for its own sake. A system that produces confident-
looking wrong accusations gets switched off within a month.

---

## Data subject rights

| Right | Endpoint / process | SLA |
|---|---|---|
| Access | Export of all records for a staff member | 30 days |
| Correction | Attendance override (audited); profile edit | 7 days |
| Deletion | Staff record + embeddings purge; aggregates retain only anonymised counts | 30 days |
| Consent withdrawal | `DELETE /staff/{id}/consent` → schedules purge | Immediate effect, purge ≤30 days |
| Objection | Switch to NFC-only attendance | Immediate |

Name an owner for these requests before go-live. An unassigned process is a
missing process.

---

## Infrastructure obligations

- [ ] DPA signed with the cloud provider.
- [ ] Data residency in-country where required (India DPDP, EU GDPR).
- [ ] Encryption at rest (KMS) for DB, S3, and the embedding store specifically.
- [ ] TLS in transit; mTLS for device authentication with per-device revocable certs.
- [ ] Access to biometric and footage stores restricted by role and audit-logged.
- [ ] Breach notification runbook with the statutory clock written on it
      (72 hours under GDPR).
- [ ] Penetration test before go-live; retest annually.
- [ ] Sub-processor list maintained and disclosed.

---

## Pre-launch sign-off

Go-live requires written sign-off on all of:

- [ ] Signage installed and photographed at every entrance
- [ ] Consent forms collected for every enrolled staff member
- [ ] Face blurring verified on a sample of 100 stored snapshots — zero unblurred faces
- [ ] Retention jobs verified running and deleting
- [ ] Purge job verified by enrolling, revoking, and confirming deletion
- [ ] Deletion request rehearsed end-to-end
- [ ] Penetration test findings closed or accepted in writing
- [ ] Client briefed in writing on the estimate nature of vision counts
