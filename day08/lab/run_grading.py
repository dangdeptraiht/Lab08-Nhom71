import json
import sys
from datetime import datetime
from rag_answer import rag_answer

sys.stdout.reconfigure(encoding='utf-8')

with open('data/grading_questions.json', encoding='utf-8') as f:
    questions = json.load(f)

log = []
for q in questions:
    qid = q['id']
    question_text = q['question']
    print(f"Running {qid}: {question_text[:60]}...")
    try:
        result = rag_answer(
            question_text,
            retrieval_mode='hybrid',
            top_k_search=20,
            top_k_select=5,
            use_rerank=True,
            verbose=False,
        )
        entry = {
            'id': qid,
            'question': question_text,
            'answer': result['answer'],
            'sources': result['sources'],
            'chunks_retrieved': len(result['chunks_used']),
            'retrieval_mode': result['config']['retrieval_mode'],
            'timestamp': datetime.now().isoformat(),
        }
        print(f"  Sources: {result['sources']}")
        answer_preview = result['answer'][:150].replace('\n', ' ')
        print(f"  Answer: {answer_preview}...")
        print()
    except Exception as e:
        entry = {
            'id': qid,
            'question': question_text,
            'answer': f'PIPELINE_ERROR: {e}',
            'sources': [],
            'chunks_retrieved': 0,
            'retrieval_mode': 'hybrid',
            'timestamp': datetime.now().isoformat(),
        }
        print(f"  ERROR: {e}")
        print()
    log.append(entry)

with open('logs/grading_run.json', 'w', encoding='utf-8') as f:
    json.dump(log, f, ensure_ascii=False, indent=2)

print('=' * 60)
print(f"Done! {len(log)} questions processed.")
print("Log saved to logs/grading_run.json")
