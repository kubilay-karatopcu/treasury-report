import useStore from '../lib/store.js';

export default function SectionHeader({ block }) {
  const viewMode         = useStore((s) => s.viewMode);
  const selectedBlockId  = useStore((s) => s.selectedBlockId);
  const setSelectedBlock = useStore((s) => s.setSelectedBlock);

  const isEdit = viewMode === 'edit';
  const isSelected = selectedBlockId === block.id;

  const cfg = block.config || {};
  const titleStyle = {
    fontSize:   cfg.font_size  ? `${cfg.font_size}px`   : undefined,
    color:      cfg.color      || undefined,
    fontWeight: cfg.weight     || undefined,
    textAlign:  cfg.text_align || undefined,
  };

  function handleClick(e) {
    if (!isEdit) return;
    e.stopPropagation();
    setSelectedBlock(isSelected ? null : block.id);
  }

  return (
    <div
      id={`block-${block.id}`}
      data-block-id={block.id}
      className={`section-header${isEdit ? ' is-clickable' : ''}${isSelected ? ' is-selected' : ''}`}
      onClick={handleClick}
    >
      <span className="section-header-bar" />
      <h2 style={titleStyle}>{block.title || 'Başlık'}</h2>
      <span className="section-header-line" />
    </div>
  );
}
