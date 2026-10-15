import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/States";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Pagina non trovata" />
      <EmptyState title="Questa pagina non esiste" icon="search" action={<Link to="/" className="btn-primary">Torna a Oggi</Link>} />
    </>
  );
}
