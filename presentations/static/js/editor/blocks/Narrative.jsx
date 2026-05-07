import { marked } from 'marked';

// DOMPurify is not available in Phase 1; content is backend-controlled so XSS
// risk is low here. Wire up sanitization before enabling user-authored markdown.
export default function Narrative({ block }) {
  const html = marked.parse(block.config.text || '', { async: false });
  return (
    <div
      className="narrative-block"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
