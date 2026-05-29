import React from 'react';

interface Props {
  size?: number;
}

const NeuradeXLogo: React.FC<Props> = ({ size = 32 }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ flexShrink: 0 }}>
    <rect width="32" height="32" rx="7" fill="#0d1117"/>

    {/* Input → hidden connections */}
    <line x1="6" y1="10" x2="16" y2="7"  stroke="#00b386" strokeWidth="1.1" opacity="1"/>
    <line x1="6" y1="10" x2="16" y2="16" stroke="#00b386" strokeWidth="0.9" opacity="0.5"/>
    <line x1="6" y1="10" x2="16" y2="25" stroke="#00b386" strokeWidth="0.7" opacity="0.2"/>
    <line x1="6" y1="22" x2="16" y2="7"  stroke="#00b386" strokeWidth="0.7" opacity="0.2"/>
    <line x1="6" y1="22" x2="16" y2="16" stroke="#00b386" strokeWidth="0.9" opacity="0.5"/>
    <line x1="6" y1="22" x2="16" y2="25" stroke="#00b386" strokeWidth="1.1" opacity="0.7"/>

    {/* Hidden → output connections */}
    <line x1="16" y1="7"  x2="26" y2="10" stroke="#00d4a3" strokeWidth="1.6" opacity="1"/>
    <line x1="16" y1="16" x2="26" y2="10" stroke="#00b386" strokeWidth="1"   opacity="0.6"/>
    <line x1="16" y1="16" x2="26" y2="22" stroke="#00b386" strokeWidth="0.9" opacity="0.45"/>
    <line x1="16" y1="25" x2="26" y2="22" stroke="#00b386" strokeWidth="1"   opacity="0.55"/>
    <line x1="16" y1="7"  x2="26" y2="22" stroke="#00b386" strokeWidth="0.7" opacity="0.2"/>
    <line x1="16" y1="25" x2="26" y2="10" stroke="#00b386" strokeWidth="0.7" opacity="0.15"/>

    {/* Input nodes */}
    <circle cx="6" cy="10" r="2.4" fill="#00b386" opacity="0.9"/>
    <circle cx="6" cy="22" r="2.4" fill="#00b386" opacity="0.55"/>

    {/* Hidden nodes */}
    <circle cx="16" cy="7"  r="2.4" fill="#00b386" opacity="1"/>
    <circle cx="16" cy="16" r="2.4" fill="#00b386" opacity="0.65"/>
    <circle cx="16" cy="25" r="2.4" fill="#00b386" opacity="0.45"/>

    {/* Output nodes — top node is the bright signal */}
    <circle cx="26" cy="10" r="5"   fill="#00d4a3" opacity="0.18"/>
    <circle cx="26" cy="10" r="3.2" fill="#00d4a3"/>
    <circle cx="26" cy="22" r="2.4" fill="#00b386" opacity="0.45"/>
  </svg>
);

export default NeuradeXLogo;
