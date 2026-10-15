// Small shared pieces: the wordmark and a handful of line icons.

export function Wordmark({ size = "md" }) {
  return (
    <span className={`wordmark wordmark--${size}`} aria-label="MPSTME ONE">
      <span className="wordmark__name">MPSTME</span>
      <span className="wordmark__one">ONE</span>
    </span>
  );
}

const paths = {
  plus: "M12 5v14M5 12h14",
  send: "M5 12h13M12 5l7 7-7 7",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "M6 6l12 12M18 6L6 18",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  copy: "M9 9h11v11H9zM5 15V4h11",
  check: "M5 12l5 5L20 7",
  out: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11",
  link: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
};

export function Icon({ name, size = 18 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}