const React=require("react"),RDS=require("react-dom/server"),sharp=require("sharp"),fa=require("react-icons/fa");
(async()=>{for(const [n,c] of [["FaEnvelope","0B3B3C"],["FaPhoneAlt","0B3B3C"],["FaCheckCircle","FFFFFF"]]){
const svg=RDS.renderToStaticMarkup(React.createElement(fa[n],{color:"#"+c,size:256}));
await sharp(Buffer.from(svg)).png().toFile("icons/"+n+".png");}})();
