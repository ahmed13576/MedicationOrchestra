# Video Submission Script: Medication Orchestra (5-Minute YouTube Demo)

This is the exact spoken script and storyboard for your YouTube video demonstration. It has been timed to fit comfortably within the **5-minute limit** while fulfilling all Kaggle judging requirements.

---

## ⏱️ Storyboard & Narration Outline

### Part 1: The Hook & The Problem (0:00 - 1:00)
- **Visuals on Screen:** Open the Flutter app on the phone. Navigate through the home screen and profile tabs. Show a photo of a messy handwritten prescription or a box of Ecosprin/Dolo.
- **Narrator Voiceover:**
  > *"Meet Priya. She manages medications for her 68-year-old father, Rajesh, who has diabetes, hypertension, and a heart condition. Because he visits three different independent specialists, there is no shared electronic medical record. Each doctor only sees the drugs they write.*
  >
  > *Last week, Dad went to a new specialist for knee pain and got Brufen. What the doctor didn't know—and what Priya couldn't have guessed—is that Brufen reacts dangerously with his daily blood thinner, Warfarin, tripling his risk of internal bleeding. Hurried consults and fragmented local pharmacies miss these drug-drug interactions, leading to millions of preventable hospitalizations every year.*
  >
  > *We built Medication Orchestra to solve this. It's a household medication guardian that turns fragmented medical records into a safe, orchestrated daily schedule."*

---

### Part 2: The Multi-Agent Orchestrator (1:00 - 2:00)
- **Visuals on Screen:** Show a slide or a diagram of the ADK Agent Orchestration structure (IntakeAgent -> SearchGroundingAgent -> InteractionCheckerAgent -> ScheduleAlertAgent). Then, pull up the FastAPI backend terminal.
- **Narrator Voiceover:**
  > *"Why did we use AI Agents? Because traditional static applications cannot adapt, reason, or coordinate. We built Medication Orchestra using Google's Agent Development Kit, or ADK, to orchestrate a team of cooperative, specialized agents:*
  >
  > *First, our **Intake Agent** uses Gemini 2.5 Flash Vision to parse photos of handwritten or printed prescriptions, extracting the names, dosages, and complex medical instructions like 'OD' (once daily) or 'TDS' (three times daily).*
  > 
  > *Next, our **Search Grounding Agent** uses Google Search to translate local Indian brand names—like Combiflam or Pantop—into their active chemical generics.*
  >
  > *Then, our **Interaction Checker Agent** performs a RAG search over a vector index of 20,000+ known interactions, flagging severities and explaining the risk in plain, caregiver-friendly language.*
  >
  > *Finally, our **Schedule & Alert Agent** resolves timing conflicts, creates a foolproof, staggered timeline, and pushes alerts to the family."*

---

### Part 3: Live App Demo (2:00 - 3:45)
- **Visuals on Screen:** Run a live screen recording of the app:
  1. Login via Firebase Auth (Email/Password).
  2. Tap "Photograph Medicine Pack". Take a photo of an interacting pair (e.g., Warfarin + Ibuprofen/Brufen).
  3. Show the parsing screen loading.
  4. Show the **red warning alert** detailing the major bleeding risk and explaining *why* it's dangerous.
  5. Go to the "Schedule" screen and show the calculated safe time gap: Warfarin at 8 AM, Ibuprofen at 2 PM.
  6. Tap the "Emergency SOS Alert" button. Show a push notification appearing on a family member's phone.
- **Narrator Voiceover:**
  > *"Let's see it in action. I'm logging in using our secure Firebase authentication flow. I'll navigate to Dad's profile. He's currently taking Warfarin. Let's take a photo of a new prescription for Brufen.*
  >
  > *The image is sent to our Cloud Run backend. Instantly, the Intake and Grounding Agents identify the drugs. Since Warfarin and Ibuprofen conflict, our Interaction Checker immediately flags a 'Major' warning. It explains in simple terms: 'Taking both together increases bleeding risk.'*
  >
  > *But Dad needs both. Our Schedule Agent steps in, calculating a safe, staggered 6-hour time-gap. It places Warfarin at 8:00 AM and Ibuprofen at 2:00 PM. If Dad feels unwell, Priya can press the Emergency Adverse Reaction button, instantly broadcasting the full interaction report to family members via Firebase Cloud Messaging.*
  >
  > *To keep things lightning-fast, we built an offline-first caching service. If no new medications are added, the app loads local caches instantly without making redundant agent API calls."*

---

### Part 4: Technical Build & Security (3:45 - 4:30)
- **Visuals on Screen:** Open the **Antigravity IDE** editor. Scroll through `agent_security.py` showing the prompt injection checks and input sanitizers. Show the `deploy.ps1` script deploying to Cloud Run.
- **Narrator Voiceover:**
  > *"This entire app was vibe coded using Google's **Antigravity IDE**. When building concierge health agents, security is paramount. We implemented a robust 3-layer security system:*
  >
  > *First, in `agent_security.py`, all user inputs are sanitized against a strict regex character filter and blocklist to prevent prompt injection attacks.*
  >
  > *Second, Gemini output payloads are parsed and validated against strict schemas to ensure predictable behavior.*
  >
  > *Third, the backend is containerized and deployed to Google Cloud Run with scale-to-zero settings, running under a non-root user with zero-trust permissions. We monitor latency, scaling events, and error rates via a custom Google Cloud Monitoring dashboard."*

---

### Part 5: Outro & The Pitch (4:30 - 5:00)
- **Visuals on Screen:** Show the GCP Cloud Monitoring 4-chart health dashboard in the browser, showing request rates and P99 latency. Return to the app's final splash screen.
- **Narrator Voiceover:**
  > *"With Medication Orchestra, we've built a continuous guardian for geriatric care. It solves a real-world, high-stakes medical problem, keeps personal data secure, scales serverlessly for pennies, and represents the future of user-centric agentic software. Thank you!"*
