import { Route, Routes } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { Ledger } from "./pages/Ledger";
import { NewThesis } from "./pages/NewThesis";
import { IdeaDetail } from "./pages/IdeaDetail";
import { OptionsPage } from "./pages/OptionsPage";
import { Review } from "./pages/Review";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <TopBar />
      <main className="mx-auto max-w-6xl px-5 py-6">
        <Routes>
          <Route path="/" element={<Ledger />} />
          <Route path="/new" element={<NewThesis />} />
          <Route path="/ideas/:id" element={<IdeaDetail />} />
          <Route path="/ideas/:id/options" element={<OptionsPage />} />
          <Route path="/review" element={<Review />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
