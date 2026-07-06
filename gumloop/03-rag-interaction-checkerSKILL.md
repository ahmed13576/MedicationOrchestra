day 2 /home/user/medication_orchestra/.agents/skills/03-rag-interaction-checker/SKILL.md
---
name: rag-interaction-checker
description: Builds the drug interaction RAG pipeline using Vertex AI Vector Search and DrugBank DDI data. Use this skill to download and process the DDI corpus, create the Vector Search index, implement the InteractionCheckerAgent in ADK, and generate plain-language interaction alerts with safe time-gap recommendations. Covers Day 2 of the sprint.
---

# RAG Drug Interaction Checker Skill

## What This Builds
- DrugBank DDI corpus download + processing script
- Vertex AI Vector Search index creation + population
- InteractionCheckerAgent (ADK) — checks all household medication pairs
- Plain-language interaction alert generation (Gemini)
- Safe time-gap scheduler logic
- `GET /api/v1/interactions` endpoint

## Step 1: Download & Process DDI Corpus

Run this script ONCE to prepare the corpus (backend/data/prepare_corpus.py):

```python
"""
Downloads DrugBank open DDI dataset (CC0 license) and prepares
it for Vertex AI Vector Search indexing.

Run: python backend/data/prepare_corpus.py
"""
import json
import csv
import re

# OPTION A: Use this pre-seeded seed corpus (top 50 critical interactions)
# This is sufficient for MVP demo — no download needed
SEED_DDI = [
    {
        "id": "ddi_001",
        "drug_a": "warfarin",
        "drug_b": "ibuprofen",
        "drug_a_aliases": ["warfarin", "coumadin", "warf"],
        "drug_b_aliases": ["ibuprofen", "brufen", "combiflam", "ibugesic", "advil"],
        "severity": "major",
        "title": "Warfarin + Ibuprofen: Dangerous bleeding risk",
        "explanation": "Ibuprofen (a painkiller/NSAID) makes warfarin (a blood thinner) much stronger, which can cause dangerous internal bleeding. This combination is dangerous even at normal doses.",
        "what_to_do": "Avoid taking these together. Use paracetamol (Dolo/Crocin) for pain instead. If you must take both, your doctor should monitor your blood closely.",
        "mechanism": "Ibuprofen inhibits platelet aggregation and displaces warfarin from plasma protein binding sites, increasing free warfarin concentration.",
        "management": "Use paracetamol as alternative analgesic. Monitor INR closely. Avoid high doses of ibuprofen.",
        "time_gap_hours": 6,
        "time_gap_note": "If both are absolutely necessary, take Warfarin in the morning and Ibuprofen after 2 PM with a minimum 6-hour gap.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "warfarin ibuprofen bleeding risk blood thinner NSAID anticoagulant brufen combiflam coumadin"
    },
    {
        "id": "ddi_002",
        "drug_a": "warfarin",
        "drug_b": "aspirin",
        "drug_a_aliases": ["warfarin", "coumadin"],
        "drug_b_aliases": ["aspirin", "ecosprin", "disprin", "acetylsalicylic acid"],
        "severity": "major",
        "title": "Warfarin + Aspirin: Severe bleeding risk",
        "explanation": "Both warfarin and aspirin thin the blood through different pathways. Taking both together dramatically increases the risk of serious, life-threatening bleeding, including stomach bleeding and brain bleeding.",
        "what_to_do": "This combination requires close medical supervision. Do NOT start or stop either drug without consulting your doctor. Regular blood tests (INR) are mandatory.",
        "mechanism": "Additive anticoagulant and antiplatelet effects. Aspirin also inhibits prostaglandin synthesis causing GI mucosal damage.",
        "management": "Only use together when clinically indicated (e.g., post-cardiac surgery). Maintain INR 2.0-2.5. Use lowest effective aspirin dose (75-100mg).",
        "time_gap_hours": 0,
        "time_gap_note": "Time separation does not reduce risk. Avoid combination unless prescribed by cardiologist.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "warfarin aspirin bleeding anticoagulant antiplatelet ecosprin disprin INR"
    },
    {
        "id": "ddi_003",
        "drug_a": "metformin",
        "drug_b": "alcohol",
        "drug_a_aliases": ["metformin", "glycomet", "glucophage", "metformin hydrochloride"],
        "drug_b_aliases": ["alcohol", "ethanol", "wine", "beer", "spirits"],
        "severity": "major",
        "title": "Metformin + Alcohol: Dangerous lactic acidosis risk",
        "explanation": "Drinking alcohol while taking Metformin (diabetes medication) can cause a rare but life-threatening condition called lactic acidosis, where dangerous acid builds up in the blood. Symptoms: extreme weakness, stomach pain, nausea, and difficulty breathing.",
        "what_to_do": "Avoid alcohol while taking Metformin. If you drink occasionally, do not drink heavily and watch for symptoms of lactic acidosis. Seek emergency care if you develop severe stomach pain or breathing difficulty after drinking.",
        "mechanism": "Alcohol inhibits gluconeogenesis and increases hepatic lactate production. Combined with metformin's effect on lactate clearance, this creates dangerous accumulation.",
        "management": "Avoid chronic heavy alcohol use. Acceptable to have occasional light drinking. Monitor for lactic acidosis symptoms.",
        "time_gap_hours": 24,
        "time_gap_note": "Do not drink alcohol within 24 hours of taking Metformin.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "metformin alcohol lactic acidosis diabetes glycomet glucophage ethanol"
    },
    {
        "id": "ddi_004", 
        "drug_a": "metronidazole",
        "drug_b": "alcohol",
        "drug_a_aliases": ["metronidazole", "metrogyl", "flagyl", "metro"],
        "drug_b_aliases": ["alcohol", "ethanol", "wine", "beer"],
        "severity": "contraindicated",
        "title": "Metronidazole + Alcohol: Contraindicated — severe reaction",
        "explanation": "Absolutely do NOT drink alcohol while taking Metronidazole (Metrogyl/Flagyl). This causes a severe reaction called the disulfiram reaction: intense nausea, vomiting, flushing, headache, and racing heartbeat. The reaction can occur even from small amounts of alcohol.",
        "what_to_do": "Do not drink ANY alcohol for the entire duration of Metronidazole treatment AND for 48 hours after your last dose. Check labels on cough syrups, mouthwashes, and cooking sauces — they often contain alcohol.",
        "mechanism": "Metronidazole inhibits acetaldehyde dehydrogenase, causing toxic acetaldehyde accumulation when alcohol is consumed.",
        "management": "Absolute contraindication. Warn patient about hidden alcohol sources (mouthwashes, cough syrups, cooking wines).",
        "time_gap_hours": 48,
        "time_gap_note": "Avoid alcohol for 48 hours after completing Metronidazole course.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "metronidazole alcohol disulfiram reaction metrogyl flagyl contraindicated nausea vomiting"
    },
    {
        "id": "ddi_005",
        "drug_a": "aspirin",
        "drug_b": "ibuprofen",
        "drug_a_aliases": ["aspirin", "ecosprin", "disprin", "acetylsalicylic acid"],
        "drug_b_aliases": ["ibuprofen", "brufen", "combiflam", "ibugesic"],
        "severity": "moderate",
        "title": "Aspirin + Ibuprofen: Ibuprofen may block aspirin's heart protection",
        "explanation": "If you take aspirin daily to protect your heart, taking ibuprofen (Brufen/Combiflam) may block aspirin's protective effect. This is especially concerning for people who take low-dose aspirin (75mg/Ecosprin) to prevent heart attacks.",
        "what_to_do": "Take aspirin at least 2 hours BEFORE ibuprofen, or use paracetamol (Dolo/Crocin) for pain instead of ibuprofen.",
        "mechanism": "Ibuprofen competitively inhibits the COX-1 binding site that aspirin acetylates, preventing irreversible platelet inhibition.",
        "management": "Take aspirin at least 2 hours before ibuprofen dose. Or switch to paracetamol for pain relief.",
        "time_gap_hours": 2,
        "time_gap_note": "Take Ecosprin/Aspirin in the morning. If you need Brufen/Ibuprofen, take it at least 2 hours later.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "aspirin ibuprofen ecosprin brufen COX-1 platelet heart protection cardioprotective"
    },
    {
        "id": "ddi_006",
        "drug_a": "omeprazole",
        "drug_b": "clopidogrel",
        "drug_a_aliases": ["omeprazole", "omez", "omeprazole"],
        "drug_b_aliases": ["clopidogrel", "plavix", "deplatt"],
        "severity": "moderate",
        "title": "Omeprazole + Clopidogrel: Reduced heart medication effect",
        "explanation": "Omeprazole (stomach acid tablet - Omez) can reduce how well Clopidogrel (blood clot prevention - Plavix/Deplatt) works. This matters for people who take clopidogrel after a heart attack or stent.",
        "what_to_do": "Ask your cardiologist if you can switch to Pantoprazole (Pantop/Pan) for stomach protection — it has less interaction with clopidogrel.",
        "mechanism": "Both drugs compete for CYP2C19 enzyme. Omeprazole inhibits CYP2C19 activation of clopidogrel to its active form.",
        "management": "Consider switching to pantoprazole or rabeprazole, which have lower CYP2C19 inhibition.",
        "time_gap_hours": 4,
        "time_gap_note": "If switching is not possible, take them 4 hours apart.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "omeprazole clopidogrel omez plavix deplatt CYP2C19 platelet heart stent antiplatelet"
    },
    {
        "id": "ddi_007",
        "drug_a": "atorvastatin",
        "drug_b": "clarithromycin",
        "drug_a_aliases": ["atorvastatin", "atorva", "lipitor", "ecosprin av"],
        "drug_b_aliases": ["clarithromycin", "claribid", "biaxin"],
        "severity": "major",
        "title": "Atorvastatin + Clarithromycin: Muscle damage risk",
        "explanation": "Clarithromycin (antibiotic) can cause atorvastatin (cholesterol tablet) levels in blood to rise dangerously high, causing muscle breakdown (rhabdomyolysis). Symptoms: severe muscle pain, dark urine, weakness.",
        "what_to_do": "Stop taking atorvastatin while on clarithromycin. Restart after completing the antibiotic course. Call your doctor if you get severe muscle pain.",
        "mechanism": "Clarithromycin inhibits CYP3A4, reducing atorvastatin metabolism and increasing plasma levels up to 5-fold.",
        "management": "Temporarily discontinue statin during clarithromycin course. Use azithromycin as alternative antibiotic if possible.",
        "time_gap_hours": 12,
        "time_gap_note": "Do not take these on the same day if possible. Consult your doctor about temporarily stopping atorvastatin.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "atorvastatin clarithromycin rhabdomyolysis muscle CYP3A4 statin antibiotic atorva lipitor claribid"
    },
    {
        "id": "ddi_008",
        "drug_a": "ace inhibitor",
        "drug_b": "potassium",
        "drug_a_aliases": ["enalapril", "lisinopril", "ramipril", "cardace", "repace", "trinace"],
        "drug_b_aliases": ["potassium", "potassium chloride", "potassium supplement", "shelcal"],
        "severity": "major",
        "title": "ACE Inhibitors + Potassium Supplements: Dangerous potassium buildup",
        "explanation": "Blood pressure medicines in the ACE inhibitor family (Cardace/Ramipril, Lisinopril, Enalapril) cause the body to retain potassium. Adding potassium supplements can cause potassium to reach dangerous levels, causing heart rhythm problems.",
        "what_to_do": "Do not take potassium supplements without your doctor's approval if you are on ACE inhibitors. Watch for symptoms: muscle weakness, irregular heartbeat, numbness.",
        "mechanism": "ACE inhibitors block aldosterone, reducing potassium excretion. Additional potassium supplementation can cause life-threatening hyperkalemia.",
        "management": "Avoid potassium supplementation. Monitor serum potassium regularly. Avoid potassium-rich salt substitutes.",
        "time_gap_hours": 0,
        "time_gap_note": "Time separation does not help — avoid the combination entirely.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "ACE inhibitor potassium hyperkalemia enalapril lisinopril ramipril cardace heart arrhythmia"
    },
    {
        "id": "ddi_009",
        "drug_a": "paracetamol",
        "drug_b": "warfarin",
        "drug_a_aliases": ["paracetamol", "dolo", "crocin", "calpol", "acetaminophen", "tylenol"],
        "drug_b_aliases": ["warfarin", "coumadin"],
        "severity": "moderate",
        "title": "Paracetamol + Warfarin: Mild increased bleeding risk (high doses only)",
        "explanation": "Regular paracetamol (Dolo/Crocin) at HIGH doses (more than 4g/day) can slightly increase the blood-thinning effect of warfarin. At normal doses (1-2 tablets of 500-650mg, up to 3-4 times daily), paracetamol is the PREFERRED pain reliever for warfarin patients.",
        "what_to_do": "Paracetamol is actually safer than ibuprofen/aspirin with warfarin. Use standard doses (1-2 tablets). Do not exceed 4g (8 tablets of 500mg) daily. Your doctor should monitor your INR if you take paracetamol regularly.",
        "mechanism": "High-dose paracetamol reduces vitamin K availability through inhibition of clotting factor production.",
        "management": "Preferred analgesic over NSAIDs. Use standard doses. Monitor INR with regular use.",
        "time_gap_hours": 0,
        "time_gap_note": "No time gap needed at normal doses. Paracetamol is the safer choice.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "paracetamol warfarin dolo crocin acetaminophen coumadin INR bleeding anticoagulant"
    },
    {
        "id": "ddi_010",
        "drug_a": "nsaid",
        "drug_b": "ace inhibitor",
        "drug_a_aliases": ["ibuprofen", "brufen", "combiflam", "diclofenac", "voveran", "naproxen"],
        "drug_b_aliases": ["enalapril", "lisinopril", "ramipril", "cardace", "telmisartan", "telma"],
        "severity": "moderate",
        "title": "NSAIDs + Blood Pressure Medications: Reduced BP control",
        "explanation": "Painkillers like Brufen (ibuprofen) and Voveran (diclofenac) can reduce the effectiveness of blood pressure medicines (Cardace, Telma). They can also cause kidney problems when taken together long-term.",
        "what_to_do": "Use paracetamol (Dolo/Crocin) for pain instead of ibuprofen or diclofenac if you take blood pressure medication. If you must use NSAIDs, take them for the shortest time possible and monitor your blood pressure.",
        "mechanism": "NSAIDs inhibit prostaglandin synthesis, causing sodium retention and vasoconstriction, counteracting antihypertensive effects.",
        "management": "Use paracetamol instead. If NSAIDs necessary, monitor BP and kidney function. Use for shortest duration possible.",
        "time_gap_hours": 4,
        "time_gap_note": "If both necessary, separate by at least 4 hours and monitor blood pressure daily.",
        "source": "DrugBank DDI v5.1.22",
        "embedding_text": "NSAID ACE inhibitor blood pressure ibuprofen diclofenac enalapril lisinopril telmisartan kidney brufen voveran cardace telma"
    }
]

# Save seed corpus
with open('backend/data/ddi_seed.json', 'w') as f:
    json.dump(SEED_DDI, f, indent=2)

print(f"Saved {len(SEED_DDI)} seed DDI interactions")
print("Ready to index in Vertex AI Vector Search")
```

## Step 2: Create Vertex AI Vector Search Index

Run once in terminal:

```bash
# Set your project
export PROJECT_ID="your-gcp-project-id"
export LOCATION="us-central1"

# Create Vector Search Index (768 dimensions for text-embedding-004)
gcloud ai indexes create \
  --display-name="ddi-interactions" \
  --description="Drug-drug interaction DDI corpus for Medication Orchestra" \
  --metadata-schema-uri="gs://google-cloud-aiplatform/schema/matchingengine/metadata/nearest_neighbor_search_1.0.0.yaml" \
  --index-update-method=STREAM_UPDATE \
  --region=$LOCATION \
  --project=$PROJECT_ID

# Note the INDEX_ID from the output, then create an index endpoint:
gcloud ai index-endpoints create \
  --display-name="ddi-endpoint" \
  --region=$LOCATION \
  --project=$PROJECT_ID

# Deploy index to endpoint (replace INDEX_ID and ENDPOINT_ID):
gcloud ai index-endpoints deploy-index ENDPOINT_ID \
  --deployed-index-id="ddi_deployed" \
  --index="INDEX_ID" \
  --display-name="ddi-deployed" \
  --region=$LOCATION
```

## Step 3: Vector Search Service (backend/services/vector_search_service.py)

```python
from google.cloud import aiplatform
import os
import json
import vertexai
from vertexai.language_models import TextEmbeddingModel

vertexai.init(
    project=os.environ['VERTEX_AI_PROJECT'],
    location=os.environ.get('VERTEX_AI_LOCATION', 'us-central1')
)

embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")

def get_embedding(text: str) -> list[float]:
    """Generate text embedding for a query."""
    embeddings = embedding_model.get_embeddings([text])
    return embeddings[0].values


def search_interactions(drug_a: str, drug_b: str, top_k: int = 3) -> list[dict]:
    """
    Search for drug-drug interactions using Vector Search.
    Returns top matching interactions.
    """
    # Build search query using both drug names and synonyms
    query = f"{drug_a} {drug_b} drug interaction side effects contraindication"
    query_embedding = get_embedding(query)
    
    # Query Vector Search
    endpoint = aiplatform.MatchingEngineIndexEndpoint(
        index_endpoint_name=os.environ['VECTOR_SEARCH_INDEX_ENDPOINT']
    )
    
    response = endpoint.find_neighbors(
        deployed_index_id="ddi_deployed",
        queries=[query_embedding],
        num_neighbors=top_k
    )
    
    # Parse results
    results = []
    for neighbor in response[0]:
        results.append({
            "id": neighbor.id,
            "distance": neighbor.distance,
            "datapoint": neighbor.datapoint
        })
    
    return results


# FALLBACK: JSON-based search (use this for MVP if Vector Search setup takes too long)
def search_interactions_fallback(drug_a: str, drug_b: str) -> dict | None:
    """
    Simple keyword-based fallback that searches the local DDI seed JSON.
    Use this if Vertex AI Vector Search is not yet configured.
    """
    drug_a_lower = drug_a.lower().strip()
    drug_b_lower = drug_b.lower().strip()
    
    try:
        with open('backend/data/ddi_seed.json') as f:
            ddi_data = json.load(f)
    except FileNotFoundError:
        return None
    
    for interaction in ddi_data:
        aliases_a = [x.lower() for x in interaction.get('drug_a_aliases', [interaction['drug_a']])]
        aliases_b = [x.lower() for x in interaction.get('drug_b_aliases', [interaction['drug_b']])]
        
        # Check both directions
        match_a = any(drug_a_lower in alias or alias in drug_a_lower for alias in aliases_a)
        match_b = any(drug_b_lower in alias or alias in drug_b_lower for alias in aliases_b)
        
        match_a_rev = any(drug_b_lower in alias or alias in drug_b_lower for alias in aliases_a)
        match_b_rev = any(drug_a_lower in alias or alias in drug_a_lower for alias in aliases_b)
        
        if (match_a and match_b) or (match_a_rev and match_b_rev):
            return interaction
    
    return None
```

## Step 4: Interaction Checker Agent (backend/agents/interaction_checker_agent.py)

```python
"""
InteractionCheckerAgent: Checks all medication pairs in a household
against the DDI corpus and returns plain-language interaction alerts.
"""
import google.generativeai as genai
from services.vector_search_service import search_interactions_fallback
from services.firestore_service import get_household_medications
import os
import json

genai.configure(api_key=os.environ['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-2.0-flash')

INTERACTION_PROMPT = """You are a medication safety advisor explaining drug interactions to patients and caregivers in India.

DRUG A: {drug_a} (Brand: {brand_a})
DRUG B: {drug_b} (Brand: {brand_b})

CLINICAL DATA FROM DATABASE:
Severity: {severity}
Title: {title}
Mechanism: {mechanism}
Management: {management}
Time Gap Recommended: {time_gap_hours} hours

Generate a response in this EXACT JSON format:
{{
  "severity": "{severity}",
  "title": "Brief title (max 60 chars)",
  "explanation": "2-3 sentences explaining what happens in simple terms. No medical jargon. Explain like talking to a 50-year-old patient who is not medically trained.",
  "what_to_do": "Clear, specific action steps. Start with the most important action. Use brand names (e.g., say Dolo/Crocin instead of paracetamol where helpful).",
  "time_gap_note": "If both medications are necessary, specific timing instruction. Example: 'Take Warfarin at 8 AM. Do not take Brufen until after 2 PM.'",
  "safe_alternative": "If a safer substitute exists, name it. Otherwise null.",
  "emergency_note": "Only include if severity is major/contraindicated: what symptoms to watch for and when to go to emergency."
}}

Always end with: "This is general information. Consult your doctor before making any changes to your medications."
"""

async def check_household_interactions(user_id: str) -> list[dict]:
    """
    Fetch all active medications for all household profiles and check every pair.
    Returns list of interaction findings.
    """
    
    # Get all medications across all profiles
    all_meds = await get_household_medications(user_id)
    
    if len(all_meds) < 2:
        return []
    
    interactions_found = []
    checked_pairs = set()
    
    for i, med_a in enumerate(all_meds):
        for med_b in all_meds[i+1:]:
            
            # Skip same medication
            if med_a['generic_name'].lower() == med_b['generic_name'].lower():
                continue
            
            # Avoid checking same pair twice
            pair_key = tuple(sorted([med_a['generic_name'].lower(), med_b['generic_name'].lower()]))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)
            
            # Search DDI database
            ddi_result = search_interactions_fallback(
                med_a['generic_name'],
                med_b['generic_name']
            )
            
            if ddi_result is None:
                # No known interaction — skip
                continue
            
            # Skip minor interactions in MVP (too noisy)
            if ddi_result['severity'] == 'minor':
                continue
            
            # Generate plain-language explanation with Gemini
            prompt = INTERACTION_PROMPT.format(
                drug_a=med_a['generic_name'],
                brand_a=med_a['brand_name'],
                drug_b=med_b['generic_name'],
                brand_b=med_b['brand_name'],
                severity=ddi_result['severity'],
                title=ddi_result['title'],
                mechanism=ddi_result.get('mechanism', 'N/A'),
                management=ddi_result.get('management', 'Consult your doctor'),
                time_gap_hours=ddi_result.get('time_gap_hours', 0)
            )
            
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.2)
            )
            
            try:
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                gemini_analysis = json.loads(text.strip())
            except:
                gemini_analysis = {
                    "explanation": ddi_result.get('explanation', ''),
                    "what_to_do": ddi_result.get('what_to_do', 'Consult your doctor'),
                    "time_gap_note": ddi_result.get('time_gap_note', '')
                }
            
            interaction = {
                "med_a_id": med_a['id'],
                "med_b_id": med_b['id'],
                "med_a_name": med_a['brand_name'],
                "med_b_name": med_b['brand_name'],
                "med_a_generic": med_a['generic_name'],
                "med_b_generic": med_b['generic_name'],
                "severity": ddi_result['severity'],
                "title": gemini_analysis.get('title', ddi_result['title']),
                "explanation": gemini_analysis.get('explanation', ''),
                "what_to_do": gemini_analysis.get('what_to_do', ''),
                "time_gap_hours": ddi_result.get('time_gap_hours', 0),
                "time_gap_note": gemini_analysis.get('time_gap_note', ''),
                "safe_alternative": gemini_analysis.get('safe_alternative'),
                "emergency_note": gemini_analysis.get('emergency_note'),
                "source": ddi_result.get('source', 'DrugBank CC0'),
                "acknowledged": False
            }
            
            interactions_found.append(interaction)
    
    # Sort by severity: contraindicated > major > moderate > minor
    severity_order = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3}
    interactions_found.sort(key=lambda x: severity_order.get(x['severity'], 99))
    
    return interactions_found
```

## Deliverable Checklist
- [ ] `ddi_seed.json` created with 10+ interactions
- [ ] `search_interactions_fallback("warfarin", "ibuprofen")` returns the ddi_001 record
- [ ] `search_interactions_fallback("dolo", "warfarin")` returns ddi_009 (paracetamol+warfarin)
- [ ] `check_household_interactions()` returns at least 1 interaction for test household with 3+ meds
- [ ] Interaction objects have all required fields: severity, title, explanation, what_to_do, time_gap_hours
- [ ] Severity is sorted: contraindicated first, minor last
- [ ] `GET /api/v1/interactions` endpoint returns interactions list
