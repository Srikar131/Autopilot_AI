import { useState, type FormEvent } from 'react';
import { FlaskConical, Plus, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import type { FormFieldSpec, QaCacheEntry } from '../types';

interface QaMemoryTabProps {
  entries: QaCacheEntry[];
  onRefresh: () => Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

export function QaMemoryTab({ entries, onRefresh, onToast }: QaMemoryTabProps) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [fieldType, setFieldType] = useState('text');
  const [portalContext, setPortalContext] = useState('general');
  const [saving, setSaving] = useState(false);

  const [testLabel, setTestLabel] = useState('What is your notice period?');
  const [testType, setTestType] = useState('text');
  const [testResult, setTestResult] = useState<{
    resolved: FormFieldSpec;
    will_trigger_hitl: boolean;
    confidence_threshold: number;
  } | null>(null);

  const handleAddEntry = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!e.currentTarget.checkValidity()) {
      e.currentTarget.reportValidity();
      return;
    }
    setSaving(true);
    try {
      await api.createQaCache({
        question_normalized: question,
        field_type: fieldType,
        canonical_answer: answer,
        portal_context: portalContext,
      });
      setQuestion('');
      setAnswer('');
      onToast('Saved verified answer into qa_cache.');
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to save qa_cache entry', true);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteQaCache(id);
      onToast('Deleted qa_cache entry.');
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to delete entry', true);
    }
  };

  const handleTestResolve = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const res = await api.resolvePreview({ label_text: testLabel, field_type: testType });
      setTestResult(res);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Resolution test failed', true);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">3-Tier Q&amp;A Semantic Memory (<code>qa_cache</code>)</h2>
        <p className="text-muted text-xs">
          Test live field resolution (1. <code>qa_cache</code> 768-dim vector match &rarr; 2. Profile Vault &rarr; 3.
          Gemini synthesis) and manage verified answers.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="card-surface rounded-2xl p-6">
          <div className="mb-4 flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-blue-500" aria-hidden="true" />
            <h3 className="text-sm font-semibold">Live 3-Tier Field Resolver Sandbox</h3>
          </div>
          <form onSubmit={handleTestResolve} className="space-y-3">
            <div>
              <label htmlFor="test-q-label" className="mb-1 block text-xs font-semibold">
                Form Field Label / Question
              </label>
              <input
                id="test-q-label"
                type="text"
                required
                value={testLabel}
                onChange={(e) => setTestLabel(e.target.value)}
                className="field-input"
              />
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1">
                <label htmlFor="test-q-type" className="mb-1 block text-xs font-semibold">
                  Field Type
                </label>
                <select
                  id="test-q-type"
                  value={testType}
                  onChange={(e) => setTestType(e.target.value)}
                  className="field-input"
                >
                  <option value="text">text</option>
                  <option value="textarea">textarea</option>
                  <option value="select">select</option>
                  <option value="number">number</option>
                </select>
              </div>
              <button
                type="submit"
                className="min-h-[44px] rounded-xl bg-blue-600 px-4 text-xs font-semibold text-white hover:bg-blue-700"
              >
                Test Resolution
              </button>
            </div>
          </form>

          {testResult && (
            <div className="muted-surface mt-4 space-y-2 rounded-xl p-4 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold">
                  Confidence: {(testResult.resolved.confidence * 100).toFixed(0)}%
                </span>
                <span
                  className={`rounded-full px-2.5 py-0.5 font-semibold ${
                    testResult.will_trigger_hitl
                      ? 'bg-amber-500/20 text-amber-700 dark:text-amber-300'
                      : 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                  }`}
                >
                  {testResult.will_trigger_hitl ? 'Triggers HITL Pause' : 'Autonomous Fill (>= 85%)'}
                </span>
              </div>
              <p>
                <strong>Proposed Value:</strong> {testResult.resolved.proposed_value || '<Empty>'}
              </p>
              <p className="text-muted">{testResult.resolved.reasoning}</p>
            </div>
          )}
        </section>

        <section className="card-surface rounded-2xl p-6">
          <h3 className="mb-4 text-sm font-semibold">Add Verified Q&amp;A Memory Entry</h3>
          <form onSubmit={handleAddEntry} className="space-y-3">
            <div>
              <label htmlFor="qa-q" className="mb-1 block text-xs font-semibold">
                Normalized Question *
              </label>
              <input
                id="qa-q"
                type="text"
                required
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="e.g. Why do you want to work at Zepto?"
                className="field-input"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="qa-type" className="mb-1 block text-xs font-semibold">
                  Field Type
                </label>
                <select
                  id="qa-type"
                  value={fieldType}
                  onChange={(e) => setFieldType(e.target.value)}
                  className="field-input"
                >
                  <option value="text">text</option>
                  <option value="textarea">textarea</option>
                  <option value="select">select</option>
                </select>
              </div>
              <div>
                <label htmlFor="qa-portal" className="mb-1 block text-xs font-semibold">
                  Portal Context
                </label>
                <input
                  id="qa-portal"
                  type="text"
                  value={portalContext}
                  onChange={(e) => setPortalContext(e.target.value)}
                  className="field-input"
                />
              </div>
            </div>
            <div>
              <label htmlFor="qa-a" className="mb-1 block text-xs font-semibold">
                Canonical Answer *
              </label>
              <textarea
                id="qa-a"
                rows={2}
                required
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                className="field-input"
              />
            </div>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex min-h-[42px] items-center gap-1.5 rounded-xl bg-blue-600 px-4 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {saving ? 'Saving...' : 'Save to qa_cache'}
            </button>
          </form>
        </section>
      </div>

      <div tabIndex={0} role="region" aria-labelledby="qa-table-caption" className="table-wrapper">
        <table className="data-table">
          <caption id="qa-table-caption" className="visually-hidden">
            Saved qa_cache entries
          </caption>
          <thead>
            <tr>
              <th>Question</th>
              <th>Canonical Answer</th>
              <th>Portal</th>
              <th>Usage</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-muted py-10 text-center text-sm">
                  No cached Q&amp;A entries yet. Add one above or resolve an HITL escalation card.
                </td>
              </tr>
            ) : (
              entries.map((item) => (
                <tr key={item.id}>
                  <th className="max-w-xs">{item.question_normalized}</th>
                  <td className="max-w-md text-xs">{item.canonical_answer}</td>
                  <td>
                    <span className="muted-surface rounded-md px-2 py-0.5 text-xs uppercase">
                      {item.portal_context}
                    </span>
                  </td>
                  <td>{item.usage_count}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => handleDelete(item.id)}
                      aria-label={`Delete ${item.question_normalized}`}
                      className="text-muted p-1.5 hover:text-red-500"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
