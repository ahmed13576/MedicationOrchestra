day 1-2 home/user/medication_orchestra/.agents/skills/02-gemini-vision-intake/SKILL.md
---
name: gemini-vision-intake
description: Builds the prescription photo scanning pipeline using Gemini Vision. Use this skill to implement the /api/v1/medications/scan endpoint, the Gemini Vision OCR logic, Indian brand name to generic mapping, and the Flutter camera screen with scan result review. Covers Day 1 Hours 2-8 of the sprint.
---

# Gemini Vision Prescription Intake Skill

## What This Builds
- `POST /api/v1/medications/scan` — takes a prescription photo, returns structured medication list
- `POST /api/v1/medications/manual` — add medication manually
- Flutter: CameraScreen → ScanResultScreen (with edit capability before saving)
- Indian brand→generic name mapping (Dolo 650→Paracetamol, etc.)

## Step 1: Gemini Service (backend/services/gemini_service.py)

```python
import google.generativeai as genai
import json
import base64
import os
from PIL import Image
import io

genai.configure(api_key=os.environ['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-2.0-flash')

PRESCRIPTION_EXTRACTION_PROMPT = """
You are a medical prescription parser specialized in Indian medical prescriptions.

Analyze this prescription image and extract ALL medications prescribed.

IMPORTANT INSTRUCTIONS:
1. Read both printed AND handwritten text carefully
2. Identify Indian prescription notations:
   - OD = Once daily → timing: ["08:00"]
   - BD = Twice daily → timing: ["08:00", "20:00"]
   - TDS = Three times daily → timing: ["08:00", "14:00", "20:00"]
   - QID = Four times daily → timing: ["08:00", "12:00", "16:00", "20:00"]
   - SOS = As needed (when required)
   - HS = At bedtime → timing: ["22:00"]
   - AC = Before meals
   - PC = After meals
   - STAT = Take immediately
3. Extract both brand name (e.g. "Dolo 650") and generic if visible (e.g. "Paracetamol")
4. If handwriting is unclear, make your best interpretation and mark confidence as "low"
5. Dosage examples: "1-0-1" means morning-afternoon-evening (BD), "1-1-1" means TDS

Return a JSON array ONLY (no other text):
[
  {
    "brand_name": "Dolo 650",
    "generic_name": "Paracetamol",
    "dosage": "650mg",
    "frequency_raw": "BD",
    "frequency_english": "Twice daily",
    "timing": ["08:00", "20:00"],
    "instruction": "After meals",
    "duration": "5 days",
    "condition": "Fever",
    "confidence": "high",
    "notes": ""
  }
]

If no medications found: []
If not a prescription image: {"error": "Not a prescription image"}
"""

INDIAN_BRAND_MAP = {
    "dolo": "Paracetamol (Acetaminophen)",
    "dolo 650": "Paracetamol (Acetaminophen) 650mg",
    "crocin": "Paracetamol (Acetaminophen)",
    "calpol": "Paracetamol (Acetaminophen)",
    "combiflam": "Ibuprofen + Paracetamol",
    "brufen": "Ibuprofen",
    "ibugesic": "Ibuprofen",
    "ecosprin": "Aspirin (Acetylsalicylic Acid)",
    "disprin": "Aspirin",
    "azithral": "Azithromycin",
    "zithromax": "Azithromycin",
    "augmentin": "Amoxicillin + Clavulanic Acid",
    "amoxyclav": "Amoxicillin + Clavulanic Acid",
    "metrogyl": "Metronidazole",
    "flagyl": "Metronidazole",
    "pantop": "Pantoprazole",
    "pan": "Pantoprazole",
    "razo": "Rabeprazole",
    "omez": "Omeprazole",
    "telma": "Telmisartan",
    "amlong": "Amlodipine",
    "amlip": "Amlodipine",
    "glycomet": "Metformin",
    "glucophage": "Metformin",
    "glimisave": "Glimepiride",
    "amaryl": "Glimepiride",
    "ecosprin av": "Aspirin + Atorvastatin",
    "atorva": "Atorvastatin",
    "lipitor": "Atorvastatin",
    "ciplar": "Propranolol",
    "lasix": "Furosemide",
    "shelcal": "Calcium + Vitamin D3",
    "neurobion": "Vitamin B Complex",
    "becosules": "Vitamin B Complex",
    "limcee": "Vitamin C (Ascorbic Acid)",
    "clavulin": "Amoxicillin + Clavulanic Acid",
    "taxim": "Cefotaxime",
    "cefixime": "Cefixime",
    "zifi": "Cefixime",
    "sporidex": "Cephalexin",
    "clavam": "Amoxicillin + Clavulanic Acid",
}

def map_brand_to_generic(brand_name: str) -> str:
    """Map Indian brand name to generic name."""
    key = brand_name.lower().strip()
    # Try exact match
    if key in INDIAN_BRAND_MAP:
        return INDIAN_BRAND_MAP[key]
    # Try prefix match (e.g., "Dolo 650mg" → "dolo")
    for brand, generic in INDIAN_BRAND_MAP.items():
        if key.startswith(brand):
            return generic
    # Return original if no mapping found
    return brand_name


async def parse_prescription(image_bytes: bytes) -> list[dict]:
    """Parse prescription image using Gemini Vision."""
    
    # Convert bytes to PIL Image
    image = Image.open(io.BytesIO(image_bytes))
    
    # Call Gemini Vision
    response = model.generate_content(
        [PRESCRIPTION_EXTRACTION_PROMPT, image],
        generation_config=genai.types.GenerationConfig(
            temperature=0.1,  # Low temperature for consistent extraction
            max_output_tokens=2048,
        )
    )
    
    # Parse JSON response
    response_text = response.text.strip()
    
    # Clean up if Gemini wraps in markdown code blocks
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
    
    try:
        medications = json.loads(response_text)
    except json.JSONDecodeError:
        return []
    
    if isinstance(medications, dict) and "error" in medications:
        raise ValueError(medications["error"])
    
    # Apply brand→generic mapping
    for med in medications:
        if not med.get("generic_name") or med["generic_name"] == med["brand_name"]:
            med["generic_name"] = map_brand_to_generic(med["brand_name"])
    
    return medications
```

## Step 2: Scan Endpoint (add to backend/main.py)

```python
from services.gemini_service import parse_prescription
from google.cloud import firestore
import uuid
from datetime import datetime

db = firestore.Client()

@app.post("/api/v1/medications/scan")
async def scan_prescription(
    image: UploadFile = File(...),
    profile_id: str = Form(...),
    user_id: str = Depends(verify_firebase_token)  # See auth section
):
    """Scan prescription photo and extract medications."""
    
    # Read image bytes
    image_bytes = await image.read()
    
    # Validate size (max 10MB)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large. Max 10MB.")
    
    # Parse with Gemini Vision
    try:
        medications = await parse_prescription(image_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    
    if not medications:
        raise HTTPException(422, "No medications found in image.")
    
    # Return extracted medications for user review (don't save yet)
    return {
        "medications": medications,
        "count": len(medications),
        "profile_id": profile_id
    }


@app.post("/api/v1/medications/confirm")
async def confirm_medications(
    payload: dict,
    user_id: str = Depends(verify_firebase_token)
):
    """Save confirmed medications to Firestore after user review."""
    
    profile_id = payload["profile_id"]
    medications = payload["medications"]
    saved_ids = []
    
    for med in medications:
        med_id = str(uuid.uuid4())
        med_data = {
            **med,
            "status": "active",
            "source": "photo",
            "created_at": datetime.utcnow(),
        }
        
        db.collection("users").document(user_id)\
          .collection("profiles").document(profile_id)\
          .collection("medications").document(med_id)\
          .set(med_data)
        
        saved_ids.append(med_id)
    
    return {"saved": len(saved_ids), "med_ids": saved_ids}
```

## Step 3: Firebase Auth Middleware (backend/services/auth_service.py)

```python
import firebase_admin
from firebase_admin import auth, credentials
import json
import os
from fastapi import HTTPException, Header

if not firebase_admin._apps:
    service_account_info = json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT'])
    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)

async def verify_firebase_token(authorization: str = Header(...)) -> str:
    """Verify Firebase ID token and return user_id."""
    try:
        token = authorization.replace("Bearer ", "")
        decoded_token = auth.verify_id_token(token)
        return decoded_token['uid']
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
```

## Step 4: Flutter Camera Screen (lib/screens/camera_screen.dart)

```dart
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:dio/dio.dart';
import 'dart:io';
import 'scan_result_screen.dart';

class CameraScreen extends StatefulWidget {
  final String profileId;
  const CameraScreen({required this.profileId, super.key});

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  final ImagePicker _picker = ImagePicker();
  bool _isProcessing = false;

  Future<void> _captureAndScan(ImageSource source) async {
    final XFile? photo = await _picker.pickImage(
      source: source,
      imageQuality: 90,       // Good quality for OCR
      preferredCameraDevice: CameraDevice.rear,
    );

    if (photo == null) return;

    setState(() => _isProcessing = true);

    try {
      final file = File(photo.path);
      final formData = FormData.fromMap({
        'image': await MultipartFile.fromFile(file.path, filename: 'prescription.jpg'),
        'profile_id': widget.profileId,
      });

      // Get Firebase token
      final token = await FirebaseAuth.instance.currentUser?.getIdToken();

      final response = await Dio().post(
        '${ApiConfig.baseUrl}/api/v1/medications/scan',
        data: formData,
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );

      if (!mounted) return;
      
      // Navigate to review screen
      Navigator.push(context, MaterialPageRoute(
        builder: (_) => ScanResultScreen(
          medications: List<Map<String, dynamic>>.from(response.data['medications']),
          profileId: widget.profileId,
        ),
      ));
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error scanning prescription: ${e.toString()}')),
      );
    } finally {
      setState(() => _isProcessing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Scan Prescription'),
      ),
      body: _isProcessing
          ? const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  CircularProgressIndicator(color: Colors.white),
                  SizedBox(height: 16),
                  Text('Reading prescription...', style: TextStyle(color: Colors.white)),
                ],
              ),
            )
          : Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.camera_alt, size: 80, color: Colors.white54),
                  const SizedBox(height: 32),
                  ElevatedButton.icon(
                    onPressed: () => _captureAndScan(ImageSource.camera),
                    icon: const Icon(Icons.camera_alt),
                    label: const Text('Take Photo of Prescription'),
                    style: ElevatedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                    ),
                  ),
                  const SizedBox(height: 16),
                  TextButton.icon(
                    onPressed: () => _captureAndScan(ImageSource.gallery),
                    icon: const Icon(Icons.photo_library, color: Colors.white54),
                    label: const Text('Choose from Gallery', 
                      style: TextStyle(color: Colors.white54)),
                  ),
                  const SizedBox(height: 32),
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 32),
                    child: Text(
                      '💡 Tip: Use flash for handwritten prescriptions. Keep the image flat and well-lit.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white38, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}
```

## Deliverable Checklist
- [ ] `parse_prescription()` returns correct JSON for a printed prescription photo
- [ ] `parse_prescription()` returns correct JSON for a handwritten prescription (test with doctor's scrawl)
- [ ] `map_brand_to_generic("Dolo 650")` returns `"Paracetamol (Acetaminophen) 650mg"`
- [ ] `POST /api/v1/medications/scan` endpoint returns 200 with medications array
- [ ] Flutter CameraScreen opens camera, takes photo, shows loading spinner
- [ ] After scan: navigates to ScanResultScreen with extracted medications
- [ ] Invalid image returns 400 error with user-friendly message
