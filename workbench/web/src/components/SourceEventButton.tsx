type Props = {
  eventId: string;
  onSelect: (eventId: string) => void;
};

export function SourceEventButton({ eventId, onSelect }: Props) {
  return (
    <button
      type="button"
      className="source-event"
      data-testid={`open-source-${eventId}`}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onSelect(eventId);
      }}
    >
      {eventId}
    </button>
  );
}
