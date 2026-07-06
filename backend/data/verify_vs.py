import sys
sys.path.insert(0, '.')
from services.vector_search_service import get_interaction, search_interactions_fallback, VECTOR_SEARCH_AVAILABLE

# Test 1: warfarin + ibuprofen (in corpus from DrugBank)
result = search_interactions_fallback('warfarin', 'ibuprofen')
assert result is not None, 'warfarin+ibuprofen must be found'
assert result['severity'] in ('contraindicated','major','moderate','minor'), 'bad severity'
assert 'description' in result
print("PASS: warfarin+ibuprofen found, severity=" + result['severity'])
print("  desc: " + result['description'][:80])

# Test 2: reverse direction
result2 = search_interactions_fallback('ibuprofen', 'warfarin')
assert result2 is not None, 'Reverse lookup must work'
print("PASS: ibuprofen+warfarin (reverse) found")

# Test 3: get_interaction wrapper
results = get_interaction('warfarin', 'ibuprofen')
assert len(results) > 0, 'get_interaction must return at least 1 result'
print("PASS: get_interaction wrapper returns " + str(len(results)) + " result(s)")

# Test 4: no match case
none_result = search_interactions_fallback('saline', 'water')
assert none_result is None, 'Unknown pair must return None, got: ' + str(none_result)
print("PASS: saline+water correctly returns None")

print("Vector Search available: " + str(VECTOR_SEARCH_AVAILABLE))
print("ALL VECTOR SEARCH SERVICE TESTS PASSED")
