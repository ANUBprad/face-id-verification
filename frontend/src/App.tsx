import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import HowItWorks from "./components/HowItWorks";
import VerificationWorkspace from "./components/VerificationWorkspace";
import Footer from "./components/Footer";
import { useVerification } from "./hooks/useVerification";

export default function App() {
  const verification = useVerification();

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Navbar />
      <main id="main">
        <Hero verification={verification} />
        <VerificationWorkspace verification={verification} />
        <HowItWorks />
      </main>
      <Footer />
    </>
  );
}