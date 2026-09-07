import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import HowItWorks from "./components/HowItWorks";
import VerificationWorkspace from "./components/VerificationWorkspace";
import Footer from "./components/Footer";

export default function App() {
  return (
    <>
      <a className="skip-link" href="#top">
        Skip to content
      </a>
      <Navbar />
      <main id="top">
        <Hero />
        <HowItWorks />
        <VerificationWorkspace />
      </main>
      <Footer />
    </>
  );
}