import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import HowItWorks from "./components/HowItWorks";
import VerificationWorkspace from "./components/VerificationWorkspace";

export default function App() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <HowItWorks />
        <VerificationWorkspace />
      </main>
    </>
  );
}