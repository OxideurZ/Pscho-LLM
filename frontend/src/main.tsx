import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";

function App() {
  return (
    <main className="shell">
      <header>
        <span className="mark" aria-hidden="true">P</span>
        <div><h1>Psych-local</h1><p>Conversation locale et privée</p></div>
      </header>
      <section className="empty">
        <h2>Un espace pour réfléchir à voix haute.</h2>
        <p>Le moteur local sera connecté à l’étape suivante.</p>
      </section>
      <form className="composer">
        <textarea aria-label="Message" placeholder="Écrire un message…" disabled />
        <button type="submit" disabled>Envoyer</button>
      </form>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);

