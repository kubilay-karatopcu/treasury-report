export default function SectionHeader({ block }) {
  return (
    <div
      id={`block-${block.id}`}
      data-block-id={block.id}
      className="section-header"
    >
      <span className="section-header-bar" />
      <h2>{block.title || 'Başlık'}</h2>
      <span className="section-header-line" />
    </div>
  );
}
