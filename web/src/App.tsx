import { Navigate, Route, Routes } from "react-router-dom";
import Header from "./components/Header";
import { Configuration, Findings, Rules } from "./routes/Placeholder";

/**
 * The shell: header + exactly three tab routes. `/` and any unknown path land on Findings.
 * Shape cited from culture-rules web/src/App.tsx @ b7f861f690b57c4355f81371cc02ad1dd2ae9cac.
 */
export function App() {
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="app__frame">
        <Header />
        <main id="main" className="page">
          <Routes>
            <Route path="/findings" element={<Findings />} />
            <Route path="/configuration" element={<Configuration />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="*" element={<Navigate to="/findings" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default App;
