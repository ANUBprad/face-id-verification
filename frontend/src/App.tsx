import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import HowItWorks from "./components/HowItWorks";
import VerificationWorkspace from "./components/VerificationWorkspace";
import Footer from "./components/Footer";

export default function App() {
  return (
    <>
      <Navbar />
      <main>
        <Hero />
        <HowItWorks />
        <VerificationWorkspace />
      </main>
      <Footer />
    </>
  );
}