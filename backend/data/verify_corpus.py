import json

processed = json.load(open('data/ddi_processed.json', encoding='utf-8'))
assert len(processed) > 1000
e = processed[0]
required = ['id','drug_a','drug_b','severity','description','embedding_text']
assert all(k in e for k in required)
assert all(processed[i]['drug_a'] and processed[i]['drug_b'] for i in range(100))
lines = open('data/ddi_index.jsonl', encoding='utf-8').readlines()
assert len(lines) > 1000
first = json.loads(lines[0])
assert 'id' in first and 'embedding_text' in first
sev_sample = set(r['severity'] for r in processed[:500])
print("PASS: " + str(len(processed)) + " records, " + str(len(lines)) + " JSONL lines")
print("drug_a=" + e['drug_a'] + " drug_b=" + e['drug_b'] + " severity=" + e['severity'])
print("Severity sample: " + str(sev_sample))
