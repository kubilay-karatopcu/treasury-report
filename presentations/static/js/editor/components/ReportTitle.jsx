export default function ReportTitle({ meta }) {
  const eyebrow = meta.eyebrow || 'Treasury Studio';
  const title   = meta.title   || 'Başlıksız Sunum';
  const date    = meta.date;
  const author  = meta.author_label || meta.author;

  const authorLine = [date, author].filter(Boolean).join(' · ');

  return (
    <div className="report-title">
      <div className="report-title-eyebrow">{eyebrow}</div>
      <h1 className="report-title-h1">{title}</h1>
      {authorLine && <div className="report-title-author">{authorLine}</div>}
    </div>
  );
}
