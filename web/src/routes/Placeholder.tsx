import { useEffect } from "react";

/**
 * One tab's page until its screen is built: the heading and one line saying so. No sample
 * data on it, ever (the canvas's data is sample content).
 */
export function Placeholder({ title, what }: { title: string; what: string }) {
  useEffect(() => {
    document.title = `${title} - qodo`;
  }, [title]);
  return (
    <>
      <h1 className="page__title">{title}</h1>
      <p className="page__lede">{what} is not built yet.</p>
    </>
  );
}

export const Findings = () => <Placeholder title="Findings" what="The findings screen" />;
export const Configuration = () => (
  <Placeholder title="Configuration" what="The configuration screen" />
);
export const Rules = () => <Placeholder title="Rules" what="The rules screen" />;
