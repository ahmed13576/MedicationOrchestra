# Privacy notice

_Version 1.0 · draft, not yet published · Digital Personal Data Protection Act, 2023_

This notice is written to be read by the person whose data it describes, not by
a lawyer. If any sentence here is unclear, that is our fault — write to the
grievance officer below and we will rewrite it.

## Who we are

Medication Orchestra ("we") is the Data Fiduciary for the information described
here. Contact details are at the end of this notice.

## What this app does with your information

You tell the app which medicines a member of your household takes, either by
photographing a prescription or strip, or by typing them in. The app checks
those medicines against a medicine knowledge base for clashes, duplicate
ingredients, dose ceilings and recorded allergies, and builds a daily timetable.

**The app is not a doctor and does not give medical advice.** It reports what
the rules say and tells you to speak to your prescriber or pharmacist.

## What we collect

| What | Why | Do we have to have it? |
|---|---|---|
| Your phone number or email | to sign you in and keep your household separate from everyone else's | yes |
| Names of household members you add (a profile name, such as "Dad") | to keep each person's medicines apart | yes |
| Medicine names, dosages and instructions | to run the safety checks and build the timetable | yes |
| Allergies you record | to warn you before a medicine you told us to avoid is scheduled | optional |
| Meal times you enter | to place doses before, with or after food | optional |
| Emergency contacts you add | to alert them when the app finds a serious clash | optional |
| Your device identifier | to send you alerts | optional |
| A record of each time health data was read or written | so you can see what happened to your data, and so we can prove it | yes |

**Photographs are not stored.** A prescription or strip photo is read, the text
is taken out, and the image is discarded. Photos are re-encoded before
processing, which removes location and camera information.

We do not collect your location, contacts list, browsing history or anything
about your health beyond the medicines and allergies you enter.

## Why we are allowed to use it (purpose)

We use your information only for the purposes you consent to:

* **Medication review** — running the safety checks and building the timetable.
* **Reading a photo** — extracting text from a prescription or strip you upload.
* **Sharing with a caregiver** — showing a family member you added the
  information you chose to share.
* **Emergency contacts** — alerting the people you listed when the app finds a
  serious clash.

You can give each of these separately, and you can withdraw any of them. If you
withdraw one, the app stops that processing on your very next request — it does
not finish what it started.

## Recording data about someone else

Many households record medicines for a parent or a child. When you do, you are
telling us you have the authority to do so, and we record that you consented on
their behalf. The person concerned may ask us for their information or ask us to
erase it, and we will act on their request.

## Who else sees your information

We do not sell your information, we do not share it for advertising, and there
is no analytics or advertising code in the app. The only third parties involved
are the services that run the product:

| Who | What they see | Why |
|---|---|---|
| Google Cloud (Firestore, Cloud Run) | everything you store in the app | it is where the app and its database run |
| Google Vertex AI (Gemini) | the prescription photo and its text; the wording of a warning we ask it to rephrase | reading a photo and putting a warning into plain language |
| Firebase Authentication | your phone number or email | signing you in |
| Firebase Cloud Messaging | your device identifier and the alert text | delivering alerts |

The safety decisions themselves are never made by an AI model. The model reads
photographs and rewrites sentences; what counts as a clash is decided by fixed,
published rules.

## Where your information is kept

In India. The database and the backend run in Google Cloud's Mumbai region
(`asia-south1`). Our deployment scripts refuse to start the service anywhere
else.

## How long we keep it

* While your account is open.
* If you withdraw consent, we erase your health data 30 days later, so you can
  change your mind.
* If you delete your data, it goes immediately.
* The record of what was done with your data (which contains no health
  information) is kept so that a deletion can be proved.

## Your rights

* **See it.** Export everything we hold — profiles, medicines, settings and the
  full history — as one file.
* **Correct it.** Edit or remove any medicine, allergy or contact at any time.
* **Erase it.** Delete everything. We confirm the deletion only after checking
  that every store is actually empty; if anything remains, we tell you instead
  of claiming success.
* **Withdraw consent.** As easily as you gave it, from the same screen.
* **Nominate someone.** You may nominate a person to exercise these rights if
  you are unable to.
* **Complain.** To our grievance officer first, and to the Data Protection Board
  of India if we do not resolve it.

## Security

Every request is authenticated, one household's data can never be read by
another, client apps cannot write clinical data directly, and every access to
health data is written to a history you can export. Full detail:
`docs/SECURITY_AND_PRIVACY.md`.

## If something goes wrong

If your data is ever exposed, we will tell the Data Protection Board of India
and every affected person within 72 hours, in English and Hindi, describing what
happened, what data was involved and what you should do.

## Grievance officer

* **Name:** _to be named before launch_
* **Email:** _to be registered before launch_
* **Address:** _to be added before launch_
* **We acknowledge** within 72 hours and **resolve** within 30 days.

_These placeholders are left visible on purpose: this notice cannot be published
until they are real._

## Changes to this notice

If we change what we collect or why, we will ask for your consent again against
the new version. Your consent is always recorded against the exact version of
the notice you saw.
